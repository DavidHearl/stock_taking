from django.core.management.base import BaseCommand
from django.utils import timezone
from stock_take.models import Schedule
import datetime


class Command(BaseCommand):
    help = 'Set completion dates for existing completed schedules that don\'t have them'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days-ago',
            type=int,
            default=35,
            help='Number of days ago to set as completion date (default: 35, which is just over 1 month)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without actually updating',
        )

    def handle(self, *args, **options):
        days_ago = options['days_ago']
        dry_run = options['dry_run']
        
        # Find completed schedules without completion dates
        completed_without_dates = Schedule.objects.filter(
            status='completed',
            completed_date__isnull=True
        )
        
        if not completed_without_dates.exists():
            self.stdout.write(
                self.style.SUCCESS('No completed schedules found without completion dates.')
            )
            return
        
        # Set completion date to specified days ago
        completion_date = timezone.now() - datetime.timedelta(days=days_ago)
        
        self.stdout.write(f"Found {completed_without_dates.count()} completed schedules without completion dates:")
        
        for schedule in completed_without_dates:
            self.stdout.write(f"  - {schedule.name} (ID: {schedule.id})")
            
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN: Would set completion_date to {completion_date.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"({days_ago} days ago) for {completed_without_dates.count()} schedules"
                )
            )
        else:
            updated_count = completed_without_dates.update(completed_date=completion_date)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully set completion_date to {completion_date.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"for {updated_count} schedules"
                )
            )
            self.stdout.write(
                self.style.WARNING(
                    f"Note: Since these were set to {days_ago} days ago, they are outside the 30-day "
                    "window and new schedules can be created for these groups if needed."
                )
            )