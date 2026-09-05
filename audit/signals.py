from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.forms.models import model_to_dict
from django.core.serializers.json import DjangoJSONEncoder
from datetime import date, datetime
import json

from audit.models import log_event
from audit.middleware import get_current_user

# List of models we want to auto-log
AUTO_LOG_MODELS = [
    ('students', 'Student'),
    ('admissions', 'Applicant'),
    ('finance', 'Invoice'),
    ('finance', 'Payment'),
    ('academics', 'Term'),
    ('timetable', 'TimetableSlot'),
    ('academics', 'LessonPlan'),
    ('academics', 'ReportCard'),
    ('academics', 'ExamScore'),
    ('attendance', 'AttendanceEntry'),
    ('hr', 'StaffProfile'),
    ('welfare', 'WelfareObservation'),
    ('discipline', 'DisciplineIncident'),
    ('events', 'CalendarEvent'),
    ('communications', 'Broadcast'),
    ('finance', 'FeeStructure'),
    ('finance', 'Expense'),
    ('finance', 'Budget'),
    ('finance', 'RecurringExpense'),
]

_pending_pre_save = {}


def _snapshot_instance(instance):
    data = model_to_dict(instance)
    for key, value in data.items():
        from django.db.models.query import QuerySet
        if isinstance(value, (QuerySet, list)):
            data[key] = [getattr(obj, 'pk', obj) for obj in value]
        from django.db.models.fields.files import FieldFile
        if isinstance(value, FieldFile):
            try:
                data[key] = value.url if value and value.name else None
            except ValueError:
                data[key] = None
        elif isinstance(value, (date, datetime)):
            data[key] = value.isoformat()
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


@receiver(pre_save)
def capture_before_snapshot(sender, instance, **kwargs):
    if sender._meta.app_label == 'audit':
        return
    model_info = (sender._meta.app_label, sender._meta.object_name)
    if model_info not in AUTO_LOG_MODELS:
        return
    if instance.pk:
        _pending_pre_save[instance.pk] = _snapshot_instance(instance)


@receiver(post_save)
def auto_log_save(sender, instance, created, **kwargs):
    model_info = (sender._meta.app_label, sender._meta.object_name)
    if model_info not in AUTO_LOG_MODELS:
        return

    if sender._meta.app_label == 'audit':
        return

    user = get_current_user()
    action = "CREATE" if created else "UPDATE"

    after = _snapshot_instance(instance)
    before = _pending_pre_save.pop(instance.pk, None) if not created else None

    log_event(
        actor=user,
        action_type=action,
        model_name=sender._meta.object_name,
        object_id=instance.pk,
        description=f"{action}: {instance}",
        before=before,
        after=after,
    )

@receiver(post_delete)
def auto_log_delete(sender, instance, **kwargs):
    model_info = (sender._meta.app_label, sender._meta.object_name)
    if model_info not in AUTO_LOG_MODELS:
        return

    user = get_current_user()
    before = _snapshot_instance(instance)

    log_event(
        actor=user,
        action_type="DELETE",
        model_name=sender._meta.object_name,
        object_id=instance.pk,
        description=f"DELETE: {instance}",
        before=before,
    )
