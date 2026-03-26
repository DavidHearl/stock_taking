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
    09:30  — sync_anthill_workflow  (refresh sale details: financials, contract numbers, etc.)
    10:00  — sync_xero_sale_payments (sync Xero payment data)
    12:00  — sync_recent_customers  (midday re-sync)

Design notes:
  - Each job runs in its own daemon thread so a long-running job (e.g. upgrade_leads
    can take 60+ minutes) never blocks the scheduler loop and causes later jobs to
    be missed.
  - A per-day "fired_today" set prevents double-firing if the loop ticks through
    the same minute twice.
  - A 10-minute catch-up window: if the container restarts within 10 minutes of
    a scheduled time, the job still fires rather than being skipped entirely.
"""

import logging
import threading
import time
from datetime import date, datetime

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Schedule definition — add more entries as needed
# Each entry: (hour, minute, command_name, kwargs_dict)
# --------------------------------------------------------------------------- #
SCHEDULE = [
    (7,  0, 'upgrade_leads',              {'days': 365}),
    (8,  0, 'sync_recent_customers',      {}),
    (9,  0, 'sync_anthill_fit_dates',     {'days': 365}),
    (9, 30, 'sync_anthill_workflow',       {'days': 365}),
    (10, 0, 'sync_xero_sale_payments',    {}),
    (12, 0, 'sync_recent_customers',      {}),
]

# How many seconds past the scheduled minute we will still fire a missed job.
CATCHUP_WINDOW_SECONDS = 600  # 10 minutes


def _run_job(command: str, kwargs: dict, stdout, style) -> None:
    """Execute a management command in a background thread, logging outcome."""
    def ts():
        return datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

    stdout.write(style.NOTICE(f'[{ts()}] Running: {command}'))
    try:
        call_command(command, **kwargs)
        stdout.write(style.SUCCESS(f'[{ts()}] {command} completed successfully.'))
    except Exception as exc:
        stdout.write(style.ERROR(f'[{ts()}] {command} FAILED: {exc}'))
        logger.exception('Scheduler: %s failed', command)
        # Write an error SyncLog so the admin page always reflects the last
        # attempt, even when the command crashes before writing its own log.
        try:
            from stock_take.models import SyncLog
            SyncLog.objects.create(
                script_name=command,
                status='error',
                records_created=0,
                records_updated=0,
                errors=1,
                notes=f'Scheduler caught unhandled exception: {exc}',
            )
        except Exception:
            pass  # DB may be down — nothing more we can do


class Command(BaseCommand):
    help = 'Long-running scheduler process — replaces cron in Docker.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Scheduler started. Schedule (UTC):'))
        for h, m, cmd, _ in SCHEDULE:
            self.stdout.write(f'  {h:02d}:{m:02d}  ->  {cmd}')
        self.stdout.write('')

        # Write a startup log so we can verify the scheduler is alive
        try:
            from stock_take.models import SyncLog
            SyncLog.objects.create(
                script_name='run_scheduler',
                status='success',
                records_created=0,
                records_updated=0,
                errors=0,
                notes=f'Scheduler started at {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}',
            )
            self.stdout.write('Startup logged to SyncLog.')
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f'Could not write startup log: {exc}'))

        # Keys: (hour, minute, command_name) — reset at midnight UTC.
        fired_today: set = set()
        last_date: date = datetime.utcnow().date()

        while True:
            now = datetime.utcnow()
            today = now.date()

            # Reset the fired set at midnight UTC.
            if today != last_date:
                fired_today.clear()
                last_date = today

            for hour, minute, command, kwargs in SCHEDULE:
                key = (hour, minute, command)
                if key in fired_today:
                    continue

                # scheduled_dt is the target time today (UTC).
                scheduled_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                overdue = (now - scheduled_dt).total_seconds()

                # Fire if we're at or within the catch-up window past the target.
                if 0 <= overdue < CATCHUP_WINDOW_SECONDS:
                    fired_today.add(key)
                    thread = threading.Thread(
                        target=_run_job,
                        args=(command, kwargs, self.stdout, self.style),
                        daemon=True,
                    )
                    thread.start()

            # Sleep until the top of the next minute.
            sleep_seconds = 60 - datetime.utcnow().second
            time.sleep(max(sleep_seconds, 1))
