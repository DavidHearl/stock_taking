from django.core.management.base import BaseCommand
from django.db.models import Count
from stock_take.models import Schedule, StockTakeGroup
import re


class Command(BaseCommand):
    help = 'Clean up duplicate auto-generated schedules'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write("DRY RUN MODE - No schedules will be deleted")
        
        # Find duplicate schedules based on patterns like "Auto: Name Stock Take #2", "#3", etc.
        duplicate_pattern = re.compile(r'^Auto: (.+) Stock Take #(\d+)$')
        
        schedules_to_delete = []
        total_deleted = 0
        
        # Get all auto-generated pending schedules
        auto_schedules = Schedule.objects.filter(
            auto_generated=True,
            status='pending'
        ).order_by('name', 'scheduled_date')
        
        # Group schedules by base name (without #2, #3, etc.)
        schedule_groups = {}
        
        for schedule in auto_schedules:
            match = duplicate_pattern.match(schedule.name)
            if match:
                base_name = match.group(1)
                number = int(match.group(2))
                
                if base_name not in schedule_groups:
                    schedule_groups[base_name] = []
                schedule_groups[base_name].append((schedule, number))
            else:
                # Check if this is a base schedule (without #number)
                base_match = re.match(r'^Auto: (.+) Stock Take$', schedule.name)
                if base_match:
                    base_name = base_match.group(1)
                    if base_name not in schedule_groups:
                        schedule_groups[base_name] = []
                    schedule_groups[base_name].append((schedule, 0))  # 0 for base schedule
        
        # For each group, keep only the earliest (base) schedule
        for base_name, schedules in schedule_groups.items():
            if len(schedules) > 1:
                # Sort by number (0 for base, then 1, 2, 3, etc.)
                schedules.sort(key=lambda x: x[1])
                
                # Keep the first one (base schedule), mark others for deletion
                to_keep = schedules[0][0]
                to_delete = [s[0] for s in schedules[1:]]
                
                self.stdout.write(
                    f"Found {len(to_delete)} duplicates for '{base_name}':"
                )
                self.stdout.write(f"  Keeping: {to_keep.name} (ID: {to_keep.id})")
                
                for schedule in to_delete:
                    self.stdout.write(
                        f"  {'Would delete' if dry_run else 'Deleting'}: {schedule.name} (ID: {schedule.id})"
                    )
                    schedules_to_delete.append(schedule)
        
        # Delete the duplicates
        if not dry_run and schedules_to_delete:
            for schedule in schedules_to_delete:
                schedule.delete()
                total_deleted += 1
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN: Would delete {len(schedules_to_delete)} duplicate schedules"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully deleted {total_deleted} duplicate schedules"
                )
            )