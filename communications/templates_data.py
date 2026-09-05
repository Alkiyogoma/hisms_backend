"""
Broadcast message templates — FRD FR-COM-002/003.
"""

MESSAGE_TEMPLATES = {
    "LATE_PICKUP": {
        "subject": "Late Pickup Notification",
        "body": "Dear Parent, this is a reminder that school hours ended at 15:30. Please arrange for immediate pickup of your child. Late pickup fees may apply."
    },
    "EVENT_REMINDER": {
        "subject": "Upcoming School Event Reminder",
        "body": "Dear Parent/Guardian, we look forward to seeing you at [Event Name] scheduled for [Date] at [Time]. Your participation is highly valued."
    },
    "FEE_REMINDER": {
        "subject": "Fee Payment Reminder",
        "body": "Dear Parent, our records show an outstanding balance on your account. Please settle the pending fees by [Date] to avoid service interruption."
    },
    "SCHOOL_CLOSURE": {
        "subject": "Important: School Closure Notice",
        "body": "Dear Parents, please be informed that Hodari Christian School will be closed on [Date] due to [Reason]. Classes will resume on [Resume Date]."
    }
}
