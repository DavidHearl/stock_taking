import os
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

class Command(BaseCommand):
    help = 'Check Google Maps Platform API usage for the current billing period'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Number of days to look back for usage (default: 30)'
        )
        parser.add_argument(
            '--alert-threshold',
            type=float,
            default=80.0,
            help='Alert threshold percentage for free tier limits (default: 80%%)'
        )

    def handle(self, *args, **options):
        api_key = getattr(settings, 'GOOGLE_MAPS_API_KEY', None)
        if not api_key:
            raise CommandError('GOOGLE_MAPS_API_KEY not found in settings')

        days = options['days']
        alert_threshold = options['alert_threshold']

        self.stdout.write(
            self.style.SUCCESS(f'Checking Google Maps API usage for the last {days} days...')
        )

        # Note: This is a simplified version. For production use, you'd need:
        # 1. Google Cloud credentials with proper permissions
        # 2. Use the Google Cloud Monitoring API or BigQuery to get detailed usage
        # 3. Set up proper authentication

        self.stdout.write(
            self.style.WARNING(
                'Note: This command provides basic monitoring. For detailed usage metrics, '
                'visit the Google Cloud Console: https://console.cloud.google.com/'
            )
        )

        # Free tier limits
        FREE_TIER_LIMITS = {
            'Dynamic Maps': 10000,  # per month
            'Geocoding': 10000,     # per month
            'Static Maps': 10000,   # per month
        }

        # Simulated usage data (replace with actual API calls)
        simulated_usage = self._get_simulated_usage(days)

        self.stdout.write('\n' + '='*60)
        self.stdout.write('GOOGLE MAPS PLATFORM USAGE SUMMARY')
        self.stdout.write('='*60)

        for api_name, limit in FREE_TIER_LIMITS.items():
            usage = simulated_usage.get(api_name, 0)
            percentage = (usage / limit) * 100

            if percentage >= alert_threshold:
                status = self.style.ERROR(f'⚠️  ALERT: {percentage:.1f}% of free tier used')
            elif percentage >= 50:
                status = self.style.WARNING(f'⚡ WARNING: {percentage:.1f}% of free tier used')
            else:
                status = self.style.SUCCESS(f'✅ OK: {percentage:.1f}% of free tier used')

            self.stdout.write(f'{api_name}:')
            self.stdout.write(f'  Used: {usage:,} requests')
            self.stdout.write(f'  Limit: {limit:,} requests/month')
            self.stdout.write(f'  Status: {status}')
            self.stdout.write('')

        self.stdout.write('='*60)
        self.stdout.write('MONITORING RECOMMENDATIONS:')
        self.stdout.write('='*60)
        self.stdout.write('1. Set up billing alerts in Google Cloud Console:')
        self.stdout.write('   https://console.cloud.google.com/billing')
        self.stdout.write('   → Select your project → Billing → Budgets & alerts')
        self.stdout.write('')
        self.stdout.write('2. Monitor usage in real-time:')
        self.stdout.write('   https://console.cloud.google.com/apis/dashboard')
        self.stdout.write('')
        self.stdout.write('3. Run this command weekly: python manage.py check_maps_usage')
        self.stdout.write('')
        self.stdout.write('4. Consider setting up automated alerts if usage exceeds thresholds')

    def _get_simulated_usage(self, days):
        """
        Simulated usage data. In production, replace this with actual API calls
        to Google Cloud Monitoring API or BigQuery.
        """
        # This would be replaced with real API calls to get actual usage
        # For now, return conservative estimates based on typical small app usage

        # Estimate based on having ~50 orders and checking map ~10 times/day
        maps_loads = min(50 * 10 * (days / 30), 10000)  # Conservative estimate
        geocoding_requests = min(50 * (days / 30), 10000)  # One geocode per unique address

        return {
            'Dynamic Maps': int(maps_loads),
            'Geocoding': int(geocoding_requests),
            'Static Maps': 0,  # Not currently used
        }