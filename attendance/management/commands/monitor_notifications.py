"""
Django management command to monitor and report on notification system status.

Usage:
  python manage.py monitor_notifications              # Show current status
  python manage.py monitor_notifications --daily      # Show daily metrics
  python manage.py monitor_notifications --weekly     # Show weekly metrics
  python manage.py monitor_notifications --alert      # Check and send alerts
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from tabulate import tabulate
import json

from attendance.notification_monitoring import (
    NotificationMetrics,
    NotificationAlerting,
    NotificationDashboardData
)


class Command(BaseCommand):
    help = 'Monitor notification system health and metrics'

    def add_arguments(self, parser):
        parser.add_argument(
            '--daily',
            action='store_true',
            help='Show daily metrics'
        )
        parser.add_argument(
            '--weekly',
            action='store_true',
            help='Show weekly metrics for past 7 days'
        )
        parser.add_argument(
            '--alert',
            action='store_true',
            help='Check health and send alerts if issues detected'
        )
        parser.add_argument(
            '--json',
            action='store_true',
            help='Output as JSON'
        )

    def handle(self, *args, **options):
        """Execute the management command"""
        if options['alert']:
            self.check_and_alert()
        elif options['weekly']:
            self.show_weekly_metrics(options['json'])
        elif options['daily']:
            self.show_daily_metrics(options['json'])
        else:
            self.show_current_status(options['json'])

    def show_current_status(self, as_json=False):
        """Show current notification system status"""
        self.stdout.write(self.style.SUCCESS(' Notification System Current Status\n'))

        health = NotificationMetrics.get_notification_health_status()
        dashboard = NotificationDashboardData.get_realtime_dashboard_data()

        if as_json:
            self.stdout.write(json.dumps(health, indent=2))
            self.stdout.write(json.dumps(dashboard, indent=2))
            return

        # Status indicator
        status_icon = '' if health['status'] == 'healthy' else ''
        self.stdout.write(f"{status_icon} System Status: {health['status'].upper()}\n")

        # Today's metrics
        today = dashboard['today']
        metrics_table = [
            ['SMS Sent', today['sms']['sent'], f"of {today['sms']['total']}"],
            ['SMS Failed', today['sms']['failed'], ''],
            ['Email Sent', today['email']['sent'], f"of {today['email']['total']}"],
            ['Email Failed', today['email']['failed'], ''],
        ]
        self.stdout.write(self.style.HTTP_INFO(tabulate(
            metrics_table,
            headers=['Metric', 'Count', 'Total'],
            tablefmt='grid'
        )) + '\n')

        # Queue status
        queue = dashboard['queue']
        queue_table = [
            ['Pending SMS', queue['pending_sms']],
            ['Pending Emails', queue['pending_emails']],
            ['Total Pending', queue['total_pending']],
        ]
        self.stdout.write(self.style.HTTP_INFO(tabulate(
            queue_table,
            headers=['Queue Item', 'Count'],
            tablefmt='grid'
        )) + '\n')

        # Alerts
        if health['alerts']:
            self.stdout.write(self.style.WARNING(' ALERTS:\n'))
            for alert in health['alerts']:
                self.stdout.write(self.style.WARNING(f"  • {alert}"))
            self.stdout.write('\n')

    def show_daily_metrics(self, as_json=False):
        """Show daily metrics for today"""
        self.stdout.write(self.style.SUCCESS(' Daily Notification Metrics (Today)\n'))

        today = timezone.now().date()
        metrics = NotificationMetrics.get_daily_metrics(today)

        if as_json:
            self.stdout.write(json.dumps(metrics, indent=2))
            return

        # SMS metrics
        sms = metrics['sms']
        self.stdout.write(self.style.HTTP_INFO(' SMS Metrics:'))
        sms_table = [
            ['Total', sms['total']],
            ['Sent', sms['sent']],
            ['Failed', sms['failed']],
            ['Pending', sms['pending']],
            ['Retry', sms['retry']],
            ['Delivery Rate', f"{sms['delivery_rate']}%"],
            ['Avg Retries', sms['average_retry_count']],
        ]
        self.stdout.write(tabulate(sms_table, headers=['Metric', 'Value'], tablefmt='grid') + '\n')

        # Email metrics
        email = metrics['email']
        self.stdout.write(self.style.HTTP_INFO(' Email Metrics:'))
        email_table = [
            ['Total', email['total']],
            ['Sent', email['sent']],
            ['Failed', email['failed']],
            ['Pending', email['pending']],
            ['Delivery Rate', f"{email['delivery_rate']}%"],
            ['Avg Retries', email['average_retry_count']],
        ]
        self.stdout.write(tabulate(email_table, headers=['Metric', 'Value'], tablefmt='grid') + '\n')

        # OTP metrics
        otp = metrics['otp']
        self.stdout.write(self.style.HTTP_INFO(' OTP Metrics:'))
        otp_table = [
            ['Generated', otp['total_generated']],
            ['Verified', otp['verified']],
            ['Expired', otp['expired']],
            ['Verification Rate', f"{otp['verification_rate']}%"],
        ]
        self.stdout.write(tabulate(otp_table, headers=['Metric', 'Value'], tablefmt='grid') + '\n')

        # Cost metrics
        cost = metrics['cost']
        self.stdout.write(self.style.HTTP_INFO(' Cost Metrics:'))
        cost_table = [
            ['SMS Sent', cost['sms_sent']],
            ['SMS Cost (USD)', f"${cost['sms_cost_usd']}"],
            ['Est. Monthly Cost (USD)', f"${cost['estimated_monthly_cost_usd']}"],
            ['Emails Sent', cost['email_sent']],
        ]
        self.stdout.write(tabulate(cost_table, headers=['Metric', 'Value'], tablefmt='grid') + '\n')

    def show_weekly_metrics(self, as_json=False):
        """Show weekly metrics for past 7 days"""
        self.stdout.write(self.style.SUCCESS(' Weekly Notification Metrics (Last 7 Days)\n'))

        metrics = NotificationMetrics.get_weekly_metrics()

        if as_json:
            self.stdout.write(json.dumps(metrics, indent=2))
            return

        # Aggregate metrics
        agg = metrics['aggregate']
        self.stdout.write(self.style.HTTP_INFO(' Aggregated Metrics:'))
        agg_table = [
            ['Total SMS Sent', agg['total_sms']],
            ['Total Emails Sent', agg['total_emails']],
            ['Avg SMS Delivery Rate', f"{agg['average_sms_delivery_rate']}%"],
            ['Avg Email Delivery Rate', f"{agg['average_email_delivery_rate']}%"],
            ['Total SMS Cost (USD)', f"${agg['total_cost_usd']}"],
        ]
        self.stdout.write(tabulate(agg_table, headers=['Metric', 'Value'], tablefmt='grid') + '\n')

        # Daily breakdown
        self.stdout.write(self.style.HTTP_INFO('\n Daily Breakdown:\n'))
        daily_table = []
        for day_metrics in metrics['days']:
            daily_table.append([
                day_metrics['date'],
                day_metrics['sms']['total'],
                day_metrics['sms']['sent'],
                f"{day_metrics['sms']['delivery_rate']}%",
                day_metrics['email']['total'],
                f"{day_metrics['email']['delivery_rate']}%",
            ])

        self.stdout.write(tabulate(
            daily_table,
            headers=['Date', 'SMS Total', 'SMS Sent', 'SMS Rate', 'Email Total', 'Email Rate'],
            tablefmt='grid'
        ) + '\n')

    def check_and_alert(self):
        """Check health and send alerts"""
        self.stdout.write(self.style.HTTP_INFO(' Checking notification system health...\n'))

        health = NotificationMetrics.get_notification_health_status()

        if health['status'] == 'healthy':
            self.stdout.write(self.style.SUCCESS(' System is healthy. No alerts needed.\n'))
        else:
            self.stdout.write(self.style.WARNING(' System issues detected. Sending alerts...\n'))

            if health['alerts']:
                for alert in health['alerts']:
                    self.stdout.write(self.style.WARNING(f"  • {alert}"))
                self.stdout.write('\n')

            NotificationAlerting.send_admin_alert(health)
            self.stdout.write(self.style.SUCCESS(' Admin alerts sent.\n'))

