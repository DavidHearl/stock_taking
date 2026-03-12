"""
Management command that runs as a long-lived process, triggering scheduled
management commands at defined times each day.

Designed to replace cron-in-Docker, which suffers from environment-variable
injection issues (special chars in SECRET_KEY / DATABASE_URL break the
`printenv | sed > /etc/environment` pipeline, causing cron jobs to silently
fail).

Usage (docker-compose scheduler service):
    python manage.py run_scheduler

Schedule (UTC):
    07:00  — upgrade_leads          (promote leads with a qualifying sale, last 365 days)
    08:00  — sync_recent_customers  (sync new/updated Anthill customers, last 7 days)
    09:00  — sync_anthill_fit_dates (parse fit_from_date text into fit_date, last 365 days)
    12:00  — sync_recent_customers  (midday re-sync)
"""

import logging
import time
from datetime import datetime, timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Schedule definition — add more entries as needed
# Each entry: (hour, minute, command_name, kwargs_dict)
# --------------------------------------------------------------------------- #
SCHEDULE = [
    (7,  0, 'upgrade_leads',          {'days': 365}),
    (8,  0, 'sync_recent_customers',  {}),
    (9,  0, 'sync_anthill_fit_dates', {'days': 365}),
    (12, 0, 'sync_recent_customers',  {}),
]


def _next_run_time(hour: int, minute: int) -> datetime:
    """Return the next UTC datetime for the given hour/minute."""
    now = datetime.utcnow().replace(second=0, microsecond=0)
    candidate = now.replace(hour=hour, minute=minute)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


class Command(BaseCommand):
    help = 'Long-running scheduler process — replaces cron in Docker.'

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS('Scheduler started. Waiting for scheduled jobs...')
        )
        self.stdout.write(f'Schedule (UTC):')
        for h, m, cmd, _ in SCHEDULE:
            self.stdout.write(f'  {h:02d}:{m:02d}  ->  {cmd}')
        self.stdout.write('')

        while True:
            now = datetime.utcnow().replace(second=0, microsecond=0)

            for hour, minute, command, kwargs in SCHEDULE:
                if now.hour == hour and now.minute == minute:
                    self.stdout.write(
                        self.style.NOTICE(
                            f'[{now.strftime("%Y-%m-%d %H:%M UTC")}] '
                            f'Running: {command}'
                        )
                    )
                    try:
                        call_command(command, **kwargs)
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'[{datetime.utcnow().strftime("%H:%M UTC")}] '
                                f'{command} completed successfully.'
                            )
                        )
                    except Exception as exc:
                        self.stderr.write(
                            self.style.ERROR(
                                f'[{datetime.utcnow().strftime("%H:%M UTC")}] '
                                f'{command} FAILED: {exc}'
                            )
                        )
                        logger.exception('Scheduler: %s failed', command)

            # Sleep until the top of the next minute
            sleep_seconds = 60 - datetime.utcnow().second
            time.sleep(sleep_seconds)
