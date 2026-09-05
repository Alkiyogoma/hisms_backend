from django.contrib import admin
from .models import WelfareObservation, WelfareAcknowledgment

# Register your models here.
admin.site.register(WelfareObservation)
admin.site.register(WelfareAcknowledgment)
