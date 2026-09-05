# HODARI SMS — Complete Project Map

> **Generated:** 2026-07-06
> **Stack:** Django 5.2 + SQLite (dev) / PostgreSQL (prod)
> **Tests:** 54 passing across Items 1–17 + final hardening

---

## 1. PROJECT STRUCTURE

```
hisms_backend/
├── config/                  # Django project config
│   ├── settings.py
│   ├── urls.py              # Root URL router
│   ├── wsgi.py
│   └── asgi.py
├── core/                    # Dashboard routing, school settings
├── users/                   # Custom User model, auth, roles
├── students/                # Student CRUD, EnrollmentHistory
├── academics/               # Terms, grades, progression, lesson plans
├── admissions/              # Applicant pipeline, enrolment
├── attendance/              # Check-in/out, QR, sync, notifications
├── timetable/               # Lesson schedules, break supervision
├── finance/                 # Invoices, payments, fee structures
├── hr/                      # Staff, payroll, leave, onboarding
├── communications/          # Bulk messaging, announcements
├── discipline/              # Behaviour incidents, sanctions
├── welfare/                 # Student welfare, health, counselling
├── events/                  # School events, calendar
├── parent_portal/           # Parent-facing dashboard
├── ptc/                     # Parent-Teacher Conference
├── audit/                   # Audit logging
├── reports/                 # Aggregated cross-module reports
├── tasks/                   # Task/assignment management
├── templates/               # 163 HTML templates (shared)
├── deployment/              # Deploy scripts, configs
├── static/                  # Static assets
├── media/                   # User uploads
├── manage.py
└── requirements.txt
```

---

## 2. ALL MODELS (by app)

### 2.1 core/models.py (4 models)

#### `TimeStampedModel` (abstract)
| Field | Type | Options |
|-------|------|---------|
| `created_at` | DateTimeField | `auto_now_add=True` |
| `updated_at` | DateTimeField | `auto_now=True` |

#### `SoftStatusModel` (abstract, extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `is_active` | BooleanField | `default=True` |

#### `MediaSettings`
| Field | Type | Options |
|-------|------|---------|
| `area` | CharField | `max_length=32`, `choices=AREA_CHOICES`, `unique=True` |
| `allowed_extensions` | CharField | `max_length=500`, `default="pdf,doc,docx,..."` |
| `max_file_size_mb` | PositiveIntegerField | `default=10` |
| `max_total_size_mb` | PositiveIntegerField | `default=50` |
| `max_files` | PositiveIntegerField | `default=5` |
| `duplicate_check` | BooleanField | `default=True` |
| `min_width` | PositiveIntegerField | `default=0` |
| `min_height` | PositiveIntegerField | `default=0` |
| `is_active` | BooleanField | `default=True` |

**AREA_CHOICES:** `lesson_plans`, `student_photos`, `admission_photos`, `admission_documents`, `staff_documents`, `staff_leave`, `profile_photos`, `finance_imports`

#### `SchoolSettings` (singleton)
| Field | Type | Options |
|-------|------|---------|
| `school_name` | CharField | `max_length=100`, `default="Hodari Christian School"` |
| `attendance_threshold_warn` | PositiveIntegerField | `default=85` |
| `attendance_threshold_critical` | PositiveIntegerField | `default=75` |
| `admission_fee` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=2500.00` |
| `enable_online_inquiry` | BooleanField | `default=True` |
| `send_absentee_sms` | BooleanField | `default=True` |
| `sms_api_key` | CharField | `max_length=255`, `blank=True`, `null=True` |
| `sms_sender_id` | CharField | `max_length=20`, `default="HODARI"` |
| `pass_mark` | PositiveIntegerField | `default=50` |
| `enable_auto_report_generation` | BooleanField | `default=False` |
| `email_backend` | CharField | `max_length=255` |
| `email_host` | CharField | `max_length=255` |
| `email_port` | PositiveIntegerField | `default=587` |
| `email_use_tls` | BooleanField | `default=True` |
| `email_host_user` | CharField | `max_length=255` |
| `email_host_password` | CharField | `max_length=255` |
| `default_from_email` | EmailField | `default="noreply@hodari.ac.tz"` |
| `whatsapp_api_key` | CharField | `max_length=255` |
| `whatsapp_sender_id` | CharField | `max_length=30` |
| `pwa_domain` | URLField | `max_length=255` |
| `pwa_version` | PositiveIntegerField | `default=1` |

---

### 2.2 users/models.py (1 model)

#### `UserRole` (TextChoices)
| Value | Label |
|-------|-------|
| `super_admin` | Super Admin |
| `head_of_school` | Head of School (HOS) |
| `primary_hod` | Primary HOD |
| `ecd_hod` | ECD HOD |
| `lower_secondary_hod` | Lower Secondary HOD |
| `admin_officer` | Admin Officer |
| `finance_officer` | Finance Officer |
| `teacher` | Teacher |
| `parent` | Parent / Guardian |

#### `User` (extends AbstractUser)
| Field | Type | Options |
|-------|------|---------|
| `role` | CharField | `max_length=40`, `choices=UserRole.choices`, `default=TEACHER`, `db_index=True` |
| `profile_picture` | FileField | `upload_to="users/photos/"`, `null=True`, `blank=True` |
| `failed_login_attempts` | PositiveSmallIntegerField | `default=0` |
| `locked_until` | DateTimeField | `null=True`, `blank=True` |
| `must_change_password` | BooleanField | `default=False` |
| `departure_date` | DateField | `null=True`, `blank=True` |
| `departure_reason` | CharField | `max_length=120`, `blank=True` |

**Inherited from AbstractUser:** `username`, `first_name`, `last_name`, `email`, `password`, `is_staff`, `is_superuser`, `is_active`, `date_joined`, `last_login`, `groups`, `user_permissions`

---

### 2.3 academics/models.py (22 models + 9 enums)

#### Enums
| Name | Values |
|------|--------|
| `RecalcStatus` | `not_started`, `queued`, `running`, `completed`, `failed` |
| `ProgressionStatus` | `calculated`, `pending_hod_review`, `pending_hos_decision`, `finalized` |
| `ProgressionOutcome` | `promote`, `retain`, `promote_with_conditions`, `graduate` |
| `CalculationBasis` | `complete`, `zero_scored`, `redistributed`, `incomplete` |
| `Department` | `ECD`, `PRIMARY`, `LOWER_SECONDARY`, `ADMINISTRATION` |
| `WeekDay` | `monday`, `tuesday`, `wednesday`, `thursday`, `friday` |
| `LessonPlanStatus` | `draft`, `submitted`, `revision_requested`, `approved`, `rejected`, `missing` |
| `ScoreStatus` | `draft`, `submitted`, `approved`, `returned` |
| `ReportCardStatus` | `draft`, `pending_sign_off`, `published` |

#### `AcademicYear`
| Field | Type | Options |
|-------|------|---------|
| `name` | CharField | `max_length=20`, `unique=True` |
| `is_current` | BooleanField | `default=False` |
| `number_of_terms` | PositiveSmallIntegerField | `default=3` |
| `start_date` | DateField | `null=True`, `blank=True` |
| `end_date` | DateField | `null=True`, `blank=True` |

**Methods:** `clean()` (overlap validation), `save()` (auto-unset previous current)

#### `Room`
| Field | Type | Options |
|-------|------|---------|
| `name` | CharField | `max_length=64`, `unique=True` |
| `building` | CharField | `max_length=100`, `blank=True` |
| `capacity` | PositiveIntegerField | `null=True`, `blank=True` |
| `is_active` | BooleanField | `default=True` |

#### `GradeClass`
| Field | Type | Options |
|-------|------|---------|
| `name` | CharField | `max_length=64`, `unique=True` |
| `department` | CharField | `max_length=32`, `choices=Department.choices` |
| `max_capacity` | PositiveIntegerField | `default=25` |
| `sort_order` | PositiveIntegerField | `default=0` |

#### `Subject`
| Field | Type | Options |
|-------|------|---------|
| `name` | CharField | `max_length=80`, `unique=True` |
| `code` | CharField | `max_length=10`, `unique=True`, `null=True`, `blank=True` |
| `color` | CharField | `max_length=20`, `default="#023AA5"` |
| `department` | CharField | `max_length=32`, `choices=Department.choices` |
| `departments` | JSONField | `default=list`, `blank=True` |
| `is_active` | BooleanField | `default=True` |
| `classes` | ManyToManyField | `to=GradeClass`, `blank=True` |

#### `TimetableEntry`
| Field | Type | Options |
|-------|------|---------|
| `term` | ForeignKey | `to=Term`, `on_delete=PROTECT`, `null=True` |
| `class_name` | CharField | `max_length=64`, `db_index=True` |
| `day_of_week` | CharField | `max_length=10`, `choices=WeekDay.choices` |
| `start_time` | TimeField | |
| `end_time` | TimeField | |
| `subject` | ForeignKey | `to=Subject`, `on_delete=CASCADE` |
| `teacher` | ForeignKey | `to=User`, `on_delete=SET_NULL`, `null=True` |
| `room` | CharField | `max_length=40`, `blank=True` |

**Meta:** `unique_together = ("term", "class_name", "day_of_week", "start_time")`

#### `Term`
| Field | Type | Options |
|-------|------|---------|
| `academic_year` | ForeignKey | `to=AcademicYear`, `on_delete=PROTECT` |
| `name` | CharField | `max_length=40` |
| `is_locked` | BooleanField | `default=False` |
| `start_date` | DateField | `null=True`, `blank=True` |
| `end_date` | DateField | `null=True`, `blank=True` |
| `grading_deadline` | DateField | `null=True`, `blank=True` |
| `exam_start_date` | DateField | `null=True`, `blank=True` |
| `exam_end_date` | DateField | `null=True`, `blank=True` |

**Meta:** `unique_together = ("academic_year", "name")`
**Methods:** `clean()` (containment + overlap validation), `get_current()` (classmethod)

#### `LessonPlan`
| Field | Type | Options |
|-------|------|---------|
| `teacher` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `term` | ForeignKey | `to=Term`, `on_delete=PROTECT` |
| `class_name` | CharField | `max_length=64`, `db_index=True` |
| `subject_name` | CharField | `max_length=80`, `db_index=True` |
| `week_start_date` | DateField | `db_index=True` |
| `lesson_title` | CharField | `max_length=150`, `blank=True` |
| `objectives` | TextField | `blank=True` |
| `activities` | TextField | `blank=True` |
| `assessment_strategy` | TextField | `blank=True` |
| `resources` | TextField | `blank=True` |
| `status` | CharField | `max_length=30`, `choices=LessonPlanStatus.choices`, `default=DRAFT` |
| `submitted_at` | DateTimeField | `null=True`, `blank=True` |
| `reviewed_by` | ForeignKey | `to=User`, `null=True` |
| `reviewed_at` | DateTimeField | `null=True`, `blank=True` |
| `reviewer_feedback` | TextField | `blank=True` |

#### `LessonPlanAttachment`
| Field | Type | Options |
|-------|------|---------|
| `lesson_plan` | ForeignKey | `to=LessonPlan`, `on_delete=CASCADE` |
| `file` | FileField | `upload_to="lesson_plans/attachments/"` |
| `filename` | CharField | `max_length=255` |
| `uploaded_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |

#### `ExamTypeConfiguration`
| Field | Type | Options |
|-------|------|---------|
| `name` | CharField | `max_length=50`, `unique=True` |
| `code` | CharField | `max_length=20`, `unique=True` |
| `description` | CharField | `max_length=100`, `blank=True` |
| `weight_percentage` | DecimalField | `max_digits=5`, `decimal_places=2` |
| `max_score` | DecimalField | `max_digits=5`, `decimal_places=2`, `default=100` |
| `is_active` | BooleanField | `default=True` |
| `display_order` | PositiveIntegerField | `default=0` |

#### `ECDTemplateConfiguration`
| Field | Type | Options |
|-------|------|---------|
| `name` | CharField | `max_length=50`, `unique=True` |
| `code` | CharField | `max_length=20`, `unique=True` |
| `description` | CharField | `max_length=100`, `blank=True` |
| `is_active` | BooleanField | `default=True` |
| `display_order` | PositiveIntegerField | `default=0` |

#### `ExamScore`
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=PROTECT` |
| `term` | ForeignKey | `to=Term`, `on_delete=PROTECT` |
| `subject_name` | CharField | `max_length=80`, `db_index=True` |
| `exam_type` | CharField | `max_length=20`, `db_index=True` |
| `score` | DecimalField | `max_digits=5`, `decimal_places=2` |
| `max_score` | DecimalField | `max_digits=5`, `decimal_places=2`, `default=100` |
| `exam_type_config` | ForeignKey | `to=ExamTypeConfiguration`, `null=True` |
| `entered_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `status` | CharField | `max_length=20`, `choices=ScoreStatus.choices`, `default=DRAFT` |
| `is_locked` | BooleanField | `default=False` |
| `approved_by` | ForeignKey | `to=User`, `null=True` |
| `approved_at` | DateTimeField | `null=True`, `blank=True` |
| `hod_feedback` | TextField | `blank=True` |
| `corrected_by` | ForeignKey | `to=User`, `null=True` |
| `correction_reason` | TextField | `blank=True` |

**Meta:** `unique_together = ("student", "term", "subject_name", "exam_type")`

#### `ReportCard`
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=PROTECT` |
| `term` | ForeignKey | `to=Term`, `on_delete=PROTECT` |
| `status` | CharField | `choices=ReportCardStatus.choices`, `default=DRAFT` |
| `generated_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `signed_off_by` | ForeignKey | `to=User`, `null=True` |
| `signed_off_at` | DateTimeField | `null=True`, `blank=True` |
| `published_at` | DateTimeField | `null=True`, `blank=True` |
| `overall_average` | DecimalField | `max_digits=5`, `decimal_places=2`, `null=True` |
| `teacher_comments` | TextField | `blank=True` |
| `hos_comments` | TextField | `blank=True` |
| `attendance_days_present` | PositiveIntegerField | `default=0` |
| `attendance_days_absent` | PositiveIntegerField | `default=0` |
| `attendance_days_late` | PositiveIntegerField | `default=0` |
| `attendance_rate` | DecimalField | `max_digits=5`, `decimal_places=2`, `null=True` |
| `comments_submitted` | BooleanField | `default=False` |
| `is_ecd_report` | BooleanField | `default=False` |
| `ecd_template_type` | CharField | `max_length=20`, `blank=True` |
| `ecd_remarks` | CharField | `max_length=50`, `blank=True` |
| `parent_signed` | BooleanField | `default=False` |
| `parent_signed_at` | DateTimeField | `null=True` |
| `parent_signature_name` | CharField | `max_length=255`, `blank=True` |

**Meta:** `unique_together = ("student", "term")`

#### `ECDEvaluation`
| Field | Type | Options |
|-------|------|---------|
| `report_card` | ForeignKey | `to=ReportCard`, `on_delete=CASCADE` |
| `domain` | CharField | `max_length=100` |
| `rating` | CharField | `max_length=2`, `choices=[E,G,S,N]` |

#### `CambridgeCheckpointScore`
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=PROTECT` |
| `academic_year` | ForeignKey | `to=AcademicYear`, `on_delete=PROTECT` |
| `subject_name` | CharField | `max_length=80` |
| `score` | DecimalField | `max_digits=3`, `decimal_places=1` |
| `entered_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |

#### `ABCPaceProgress`
| Field | Type | Options |
|-------|------|---------|
| `report_card` | ForeignKey | `to=ReportCard`, `on_delete=CASCADE` |
| `subject` | CharField | `max_length=64` |
| `pace_no` | CharField | `max_length=10` |
| `sticker_no` | CharField | `max_length=10`, `blank=True` |
| `status` | CharField | `max_length=20`, `choices=[complete, in_progress]` |
| `supervisor_score` | DecimalField | `null=True` |
| `moderator_score` | DecimalField | `null=True` |
| `date_completed` | CharField | `max_length=40`, `blank=True` |

#### `ABCScripture`
| Field | Type | Options |
|-------|------|---------|
| `report_card` | ForeignKey | `to=ReportCard`, `on_delete=CASCADE` |
| `quarter` | PositiveIntegerField | `choices=[(1,"Term 1"),(2,"Term 2"),(3,"Term 3")]` |
| `verse` | CharField | `max_length=255` |

#### `ABCReadingProgramme`
| Field | Type | Options |
|-------|------|---------|
| `report_card` | ForeignKey | `to=ReportCard`, `on_delete=CASCADE` |
| `quarter` | PositiveIntegerField | `choices=[1,2,3]` |
| `wpm` | PositiveIntegerField | `null=True` |
| `percentage` | DecimalField | `null=True` |
| `comprehension_score` | DecimalField | `null=True` |

#### `ABCGeneralAssignment`
| Field | Type | Options |
|-------|------|---------|
| `report_card` | ForeignKey | `to=ReportCard`, `on_delete=CASCADE` |
| `quarter` | PositiveIntegerField | `choices=[1,2,3]` |
| `item_name` | CharField | `max_length=150` |
| `score` | DecimalField | `null=True` |

#### `ABCInternalExam`
| Field | Type | Options |
|-------|------|---------|
| `report_card` | ForeignKey | `to=ReportCard`, `on_delete=CASCADE` |
| `subject` | CharField | `max_length=64` |
| `rating` | CharField | `max_length=2`, `choices=[E,G,S,N]` |

#### `ProgressionConfig`
| Field | Type | Options |
|-------|------|---------|
| `academic_year_from` | ForeignKey | `to=AcademicYear`, `on_delete=PROTECT` |
| `academic_year_to` | ForeignKey | `to=AcademicYear`, `on_delete=PROTECT` |
| `minimum_average` | FloatField | `default=50.0` |
| `minimum_attendance` | FloatField | `default=80.0` |
| `retention_threshold` | FloatField | `default=50.0` |
| `created_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `recalc_status` | CharField | `max_length=20`, `choices=RecalcStatus.choices` |
| `recalc_completed_count` | PositiveIntegerField | `null=True` |
| `recalc_failed_details` | JSONField | `default=list` |

#### `ProgressionCase`
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=PROTECT` |
| `progression_config` | ForeignKey | `to=ProgressionConfig`, `on_delete=PROTECT` |
| `calculated_average` | FloatField | `null=True`, `blank=True` |
| `calculated_attendance_rate` | FloatField | `null=True`, `blank=True` |
| `calculation_basis` | CharField | `choices=CalculationBasis.choices`, `default=INCOMPLETE` |
| `system_suggested_outcome` | CharField | `choices=ProgressionOutcome.choices`, `null=True` |
| `hod_recommendation` | CharField | `choices=ProgressionOutcome.choices`, `null=True` |
| `hod_recommended_by` | ForeignKey | `to=User`, `null=True` |
| `hod_recommended_at` | DateTimeField | `null=True` |
| `hos_decision` | CharField | `choices=ProgressionOutcome.choices`, `null=True` |
| `hos_decided_by` | ForeignKey | `to=User`, `null=True` |
| `hos_decided_at` | DateTimeField | `null=True` |
| `override_reason` | TextField | `blank=True` |
| `status` | CharField | `choices=ProgressionStatus.choices`, `default=CALCULATED` |
| `hos_return_comment` | TextField | `blank=True` |

**Meta:** `unique_together = ("student", "progression_config")`
**Methods:** `clean()` (status transition validation), `save()` (calls clean)

**Allowed status transitions (model-layer enforced):**
```
CALCULATED → PENDING_HOD_REVIEW
PENDING_HOD_REVIEW → PENDING_HOS_DECISION
PENDING_HOS_DECISION → FINALIZED
PENDING_HOS_DECISION → PENDING_HOD_REVIEW (return path)
FINALIZED → (none — terminal)
```

#### `PromotionRun`
| Field | Type | Options |
|-------|------|---------|
| `academic_year_from` | ForeignKey | `to=AcademicYear`, `on_delete=PROTECT` |
| `academic_year_to` | ForeignKey | `to=AcademicYear`, `on_delete=PROTECT` |
| `executed_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `promoted_count` | PositiveIntegerField | `default=0` |
| `retained_count` | PositiveIntegerField | `default=0` |
| `graduated_count` | PositiveIntegerField | `default=0` |
| `status` | CharField | `max_length=20`, `default="pending"` |
| `completed_at` | DateTimeField | `null=True` |
| `processed_student_ids` | JSONField | `default=list` |
| `failed_student_ids` | JSONField | `default=list` |
| `failed_count` | PositiveIntegerField | `default=0` |
| `failed_detail_json` | JSONField | `default=list` |

---

### 2.4 students/models.py (8 models)

#### `Student`
| Field | Type | Options |
|-------|------|---------|
| `admission_no` | CharField | `max_length=20`, `unique=True` |
| `first_name` | CharField | `max_length=50` |
| `last_name` | CharField | `max_length=50` |
| `date_of_birth` | DateField | `null=True`, `blank=True` |
| `gender` | CharField | `max_length=10`, `choices=choices` |
| `class_name` | CharField | `max_length=64`, `db_index=True` |
| `stream_name` | CharField | `max_length=64`, `blank=True` |
| `academic_year` | ForeignKey | `to=AcademicYear`, `on_delete=PROTECT`, `null=True` |
| `status` | CharField | `choices=StudentStatus.choices`, `default=ACTIVE` |
| `is_archived` | BooleanField | `default=False` |
| `photo` | FileField | `upload_to="students/photos/"`, `null=True` |
| `phone_number` | CharField | `max_length=20`, `blank=True` |
| `emergency_contact_name` | CharField | `max_length=100`, `blank=True` |
| `emergency_contact_phone` | CharField | `max_length=20`, `blank=True` |
| `prev_school_name` | CharField | `max_length=255`, `blank=True` |
| `laravel_id` | PositiveIntegerField | `null=True` (Laravel bridge) |

**Meta:** indexes on `(status, class_name)`, `(academic_year, class_name)`

#### `StudentSibling`
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=CASCADE`, `related_name=siblings` |
| `sibling_admission_no` | CharField | `max_length=20` |
| `sibling_name` | CharField | `max_length=100` |
| `sibling_class` | CharField | `max_length=64`, `blank=True` |
| `relationship` | CharField | `max_length=50`, `blank=True` |

#### `ParentGuardian`
| Field | Type | Options |
|-------|------|---------|
| `first_name` | CharField | `max_length=50` |
| `last_name` | CharField | `max_length=50` |
| `phone_number` | CharField | `max_length=20` |
| `email` | EmailField | `null=True`, `blank=True` |
| `relationship` | CharField | `max_length=50` |
| `is_primary` | BooleanField | `default=False` |
| `archived_at` | DateTimeField | `null=True`, `blank=True` |
| `laravel_id` | PositiveIntegerField | `null=True` |

#### `PDPAConsentLog`
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=CASCADE` |
| `consent_given` | BooleanField | `default=False` |
| `consent_date` | DateTimeField | `auto_now_add=True` |
| `consent_source` | CharField | `max_length=20` |
| `ip_address` | GenericIPAddressField | `null=True` |
| `consent_version` | CharField | `max_length=20` |

#### `StudentGuardian`
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=CASCADE` |
| `guardian` | ForeignKey | `to=ParentGuardian`, `on_delete=CASCADE` |
| `relationship` | CharField | `max_length=50` |
| `is_primary` | BooleanField | `default=False` |
| `is_emergency_contact` | BooleanField | `default=False` |

**Meta:** `unique_together = ("student", "guardian")`

#### `LaravelParent`
| Field | Type | Options |
|-------|------|---------|
| `laravel_id` | PositiveIntegerField | `unique=True` |
| `first_name` | CharField | `max_length=50` |
| `last_name` | CharField | `max_length=50` |
| `phone` | CharField | `max_length=32` |
| `email` | EmailField | `blank=True` |
| `is_archived` | BooleanField | `default=False` |

#### `StudentLaravelParent`
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=CASCADE` |
| `laravel_parent` | ForeignKey | `to=LaravelParent`, `on_delete=CASCADE` |
| `relationship` | CharField | `max_length=50` |

#### `EnrollmentHistory`
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=CASCADE` |
| `academic_year` | ForeignKey | `to=AcademicYear`, `null=True` |
| `term` | ForeignKey | `to=Term`, `null=True` |
| `class_name` | CharField | `max_length=64`, `db_index=True` |
| `stream_name` | CharField | `max_length=64`, `blank=True` |
| `action` | CharField | `choices=[enrolled, promoted, retained, transferred, graduated]` |
| `enrolled_at` | DateField | `auto_now_add=True` |
| `notes` | TextField | `blank=True` |
| `progression_case` | ForeignKey | `to=ProgressionCase`, `null=True` |

> **Historical ambiguity (documented via code comment on action field):** Rows with `action='promoted'` created before the `retained` value existed may represent actual promotions OR retentions recorded under the old CLI script's shared `promoted` value. Check the `notes` field for "Repeating" to identify retentions. This was a deliberate decision, not an oversight.

---

### 2.5 admissions/models.py (7 models + 3 enums)

#### Enums
| Name | Values |
|------|--------|
| `InquiryChannel` | `walk_in`, `phone_call`, `website`, `social` |
| `ApplicantStatus` | `inquiry_received`, `meeting_scheduled`, `assessment_pending`, `assessment_fee_paid`, `assessment_confirmed`, `assessment_completed`, `hod_review`, `hos_decision`, `admitted`, `conditional`, `denied`, `enrolled`, `waitlisted`, `withdrawn` |
| `ApplicantDocumentType` | `birth_certificate`, `clearance_form`, `admission_form`, `fee_arrangement_proof` |

#### `AdmissionGrade` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `name` | CharField | `max_length=64`, `unique=True` |
| `department` | CharField | `max_length=32`, `choices=Department.choices`, `db_index=True` |
| `sort_order` | PositiveIntegerField | `default=100` |
| `is_active` | BooleanField | `default=True`, `db_index=True` |

**Meta:** `ordering = ["sort_order", "name"]`

#### `Applicant` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `parent_full_name` | CharField | `max_length=150` |
| `parent_phone` | CharField | `max_length=32` |
| `parent_email` | EmailField | `blank=True` |
| `parent_invoice_name` | CharField | `max_length=150`, `blank=True` |
| `child_full_name` | CharField | `max_length=150` |
| `child_date_of_birth` | DateField | |
| `grade_applying_for` | CharField | `max_length=32` |
| `previous_school` | CharField | `max_length=120`, `blank=True` |
| `previous_school_other` | CharField | `max_length=120`, `blank=True` |
| `sibling_currently_enrolled` | BooleanField | `default=False` |
| `sibling_details` | CharField | `max_length=255`, `blank=True` |
| `inquiry_channel` | CharField | `max_length=20`, `choices=InquiryChannel.choices` |
| `parent_relationship` | CharField | `max_length=20`, `choices=[father,mother,guardian,other]`, `blank=True` |
| `status` | CharField | `max_length=40`, `choices=ApplicantStatus.choices`, `db_index=True` |
| `notes` | TextField | `blank=True` |
| `photo` | ImageField | `upload_to="admissions/photos/"`, `null=True`, `blank=True` |
| `sibling_matched_parent` | ForeignKey | `to=ParentGuardian`, `null=True`, `blank=True` |
| `sibling_link_decision` | CharField | `max_length=20`, `choices=[linked,new]`, `blank=True` |
| `conditional_conditions` | TextField | `blank=True` |
| `enrolled_student` | OneToOneField | `to=Student`, `null=True`, `blank=True` |

**Meta:** `ordering = ["created_at"]`, indexes on `(status, grade_applying_for)`, `(inquiry_channel, created_at)`
**Methods:** `clean()` (validates required fields, active grade exists)

#### `ApplicantTimelineEntry` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `applicant` | ForeignKey | `to=Applicant`, `on_delete=CASCADE`, `related_name=timeline` |
| `from_status` | CharField | `max_length=40`, `choices=ApplicantStatus.choices` |
| `to_status` | CharField | `max_length=40`, `choices=ApplicantStatus.choices` |
| `actor` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `reason` | TextField | `blank=True` |

**Meta:** `ordering = ["-created_at"]`, index on `(applicant, created_at)`

#### `ApplicantDocumentReceipt` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `applicant` | ForeignKey | `to=Applicant`, `on_delete=CASCADE`, `related_name=documents` |
| `document_type` | CharField | `max_length=40`, `choices=ApplicantDocumentType.choices` |
| `is_received` | BooleanField | `default=False` |
| `received_at` | DateTimeField | `null=True`, `blank=True` |
| `received_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `file` | FileField | `upload_to="admissions/documents/"`, `null=True`, `blank=True` |

**Meta:** `unique_together = ("applicant", "document_type")`, index on `(applicant, document_type)`

#### `AssessmentSchedule` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `applicant` | OneToOneField | `to=Applicant`, `on_delete=CASCADE`, `related_name=assessment` |
| `scheduled_date` | DateField | |
| `scheduled_time` | TimeField | |
| `location` | CharField | `max_length=120` |
| `facilitating_teacher_name` | CharField | `max_length=120` |
| `logistics_sent_at` | DateTimeField | `null=True`, `blank=True` |
| `assessment_fee_amount` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `assessment_fee_confirmed_paid` | BooleanField | `default=False` |
| `assessment_fee_confirmed_at` | DateTimeField | `null=True`, `blank=True` |
| `assessment_fee_confirmed_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `teacher_comments` | TextField | `blank=True` |
| `hod_comments` | TextField | `blank=True` |
| `result` | CharField | `max_length=20`, `blank=True`, `choices=[recommended,not_yet_ready,needs_support]` |
| `hod_signed_off` | BooleanField | `default=False` |
| `hod_signed_off_at` | DateTimeField | `null=True`, `blank=True` |
| `hod_signed_off_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |

#### `ApplicantInternalNote` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `applicant` | ForeignKey | `to=Applicant`, `on_delete=CASCADE`, `related_name=internal_notes` |
| `author` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `body` | TextField | |

**Meta:** `ordering = ["-created_at"]`, index on `(applicant, created_at)`

#### `EnrolmentChecklist` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `applicant` | OneToOneField | `to=Applicant`, `on_delete=CASCADE`, `related_name=enrolment_checklist` |
| `orientation_visit_completed` | BooleanField | `default=False` |
| `orientation_visit_completed_at` | DateTimeField | `null=True`, `blank=True` |
| `orientation_visit_completed_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |

---

### 2.6 attendance/models.py (5 models)

**Models:** `AttendanceEntry`, `StaffAttendanceEntry`, `OtpCode`, `Message`, `NotificationLog`

*(Full field tables omitted — these follow standard TimeStampedModel + status tracking patterns)*

---

### 2.7 audit/models.py (1 model)

#### `AuditLog`
| Field | Type | Options |
|-------|------|---------|
| `actor` | ForeignKey | `to=User`, `null=True` |
| `action_type` | CharField | `max_length=50`, `db_index=True` |
| `app_label` | CharField | `max_length=50` |
| `model_name` | CharField | `max_length=50` |
| `object_id` | CharField | `max_length=50`, `null=True` |
| `changes` | JSONField | `default=dict` |
| `ip_address` | GenericIPAddressField | `null=True` |
| `timestamp` | DateTimeField | `auto_now_add=True`, `db_index=True` |

---

### 2.8 communications/models.py (4 models + 4 enums)

#### Enums
| Name | Values |
|------|--------|
| `NotificationCategory` | `admissions`, `attendance`, `finance`, `academic`, `welfare`, `system`, `broadcast` |
| `BroadcastAudience` | `all_parents`, `grade`, `individual`, `staff` |
| `BroadcastStatus` | `draft`, `sent` |
| `WeeklyFocusStatus` | `draft`, `submitted`, `approved`, `rejected` |

#### `Notification` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `recipient` | ForeignKey | `to=User`, `on_delete=CASCADE`, `related_name=notifications` |
| `category` | CharField | `max_length=20`, `choices=NotificationCategory.choices` |
| `title` | CharField | `max_length=200` |
| `body` | TextField | |
| `link` | CharField | `max_length=255`, `blank=True` |
| `is_read` | BooleanField | `default=False`, `db_index=True` |
| `read_at` | DateTimeField | `null=True`, `blank=True` |

**Meta:** `ordering = ["-created_at"]`, indexes on `(recipient, is_read)`, `(recipient, created_at)`

#### `Broadcast` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `created_by` | ForeignKey | `to=User`, `on_delete=PROTECT`, `related_name=broadcasts` |
| `audience` | CharField | `max_length=20`, `choices=BroadcastAudience.choices` |
| `target_grade` | CharField | `max_length=64`, `blank=True` |
| `target_user` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `subject` | CharField | `max_length=200` |
| `body` | TextField | |
| `status` | CharField | `max_length=10`, `choices=BroadcastStatus.choices` |
| `sent_at` | DateTimeField | `null=True`, `blank=True` |
| `recipient_count` | PositiveIntegerField | `default=0` |

#### `WeeklyFocus` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `teacher` | ForeignKey | `to=User`, `on_delete=PROTECT`, `related_name=weekly_focuses` |
| `class_name` | CharField | `max_length=64`, `db_index=True` |
| `week_number` | PositiveSmallIntegerField | |
| `academic_year` | CharField | `max_length=16` |
| `theme` | CharField | `max_length=200` |
| `planned_activities` | TextField | |
| `items_to_bring` | TextField | `blank=True` |
| `status` | CharField | `max_length=20`, `choices=WeeklyFocusStatus.choices`, `db_index=True` |
| `submitted_at` | DateTimeField | `null=True`, `blank=True` |
| `reviewed_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `reviewed_at` | DateTimeField | `null=True`, `blank=True` |
| `reviewer_feedback` | TextField | `blank=True` |
| `is_published` | BooleanField | `default=False`, `db_index=True` |
| `published_at` | DateTimeField | `null=True`, `blank=True` |

**Meta:** `unique_together = ("class_name", "week_number", "academic_year")`, indexes on `(class_name, academic_year)`, `(status)`

#### `PhoneOTP` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `phone` | CharField | `max_length=32`, `db_index=True` |
| `code_hash` | CharField | `max_length=64` |
| `expires_at` | DateTimeField | `db_index=True` |
| `consumed_at` | DateTimeField | `null=True`, `blank=True` |

---

### 2.9 discipline/models.py (1 model + 2 enums)

#### Enums
| Name | Values |
|------|--------|
| `IncidentSeverity` | `low`, `medium`, `high`, `critical` |
| `IncidentStatus` | `pending_review`, `under_investigation`, `resolved`, `dismissed` |

#### `DisciplineIncident` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=PROTECT`, `related_name=incidents` |
| `reported_by` | ForeignKey | `to=User`, `on_delete=PROTECT`, `related_name=reported_incidents` |
| `severity` | CharField | `max_length=20`, `choices=IncidentSeverity.choices` |
| `summary` | TextField | |
| `action_taken` | TextField | `blank=True` |
| `escalated` | BooleanField | `default=False` |
| `incident_date` | DateField | `null=True`, `blank=True`, `db_index=True` |
| `time_of_incident` | TimeField | `null=True`, `blank=True` |
| `location` | CharField | `max_length=100`, `blank=True` |
| `previous_incidents` | BooleanField | `default=False` |
| `incident_level_1` | JSONField | `default=list`, `blank=True` |
| `incident_level_2` | JSONField | `default=list`, `blank=True` |
| `incident_level_3` | JSONField | `default=list`, `blank=True` |
| `incident_level_4` | JSONField | `default=list`, `blank=True` |
| `actions_taken_detailed` | JSONField | `default=list`, `blank=True` |
| `parent_contacted` | BooleanField | `default=False` |
| `parent_contact_datetime` | DateTimeField | `null=True`, `blank=True` |
| `follow_up_required` | BooleanField | `default=False` |
| `follow_up_date` | DateField | `null=True`, `blank=True` |
| `parent_confirmed` | BooleanField | `default=False` |
| `parent_confirmation_date` | DateTimeField | `null=True`, `blank=True` |
| `status` | CharField | `max_length=30`, `choices=IncidentStatus.choices`, `db_index=True` |
| `hod_notes` | TextField | `blank=True` |
| `reviewed_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `reviewed_at` | DateTimeField | `null=True`, `blank=True` |

**Meta:** `ordering = ["-created_at"]`, indexes on `(student, status)`, `(severity, status)`

---

### 2.10 events/models.py (2 models + 1 enum)

#### Enum
| Name | Values |
|------|--------|
| `EventCategory` | `academic`, `social`, `holiday`, `other` |

#### `CalendarEvent` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `title` | CharField | `max_length=150` |
| `category` | CharField | `max_length=20`, `choices=EventCategory.choices` |
| `description` | TextField | `blank=True` |
| `start_date` | DateField | |
| `end_date` | DateField | `null=True`, `blank=True` |
| `is_public_holiday` | BooleanField | `default=False` |
| `notify_parents` | BooleanField | `default=True` |
| `notify_staff` | BooleanField | `default=True` |
| `is_published` | BooleanField | `default=True` |
| `reminder_sent` | BooleanField | `default=False` |
| `parent_acknowledgement_required` | BooleanField | `default=False` |
| `auto_generated` | BooleanField | `default=False` |
| `source_model` | CharField | `max_length=50`, `blank=True` |
| `source_id` | PositiveIntegerField | `null=True`, `blank=True` |

**Meta:** `ordering = ["start_date"]`, index on `(source_model, source_id)`

#### `EventAcknowledgement` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `event` | ForeignKey | `to=CalendarEvent`, `on_delete=CASCADE` |
| `parent` | ForeignKey | `to=User`, `on_delete=CASCADE` |
| `acknowledged_at` | DateTimeField | `auto_now_add=True` |

**Meta:** `unique_together = ("event", "parent")`

---

### 2.11 finance/models.py (14 models + 9 enums)

#### Enums
| Name | Values |
|------|--------|
| `FeeCategory` | `tuition`, `assessment`, `activity`, `uniform`, `other` |
| `SiblingDiscountMode` | `percentage`, `fixed` |
| `FeeStructureStatus` | `draft`, `published`, `locked` |
| `InvoiceStatus` | `unpaid`, `partial`, `paid`, `overdue` |
| `PaymentMethod` | `cash`, `bank_transfer`, `cheque`, `mobile_money`, `mpesa`, `tigo_pesa`, `airtel_money`, `halopesa`, `credit_card`, `debit_card`, `other` |
| `ExpenseStatus` | `pending`, `approved`, `rejected` |
| `ExpenseCategory` | `salary`, `utilities`, `supplies`, `maintenance`, `transport`, `catering`, `events`, `professional`, `rent`, `technology`, `marketing`, `insurance`, `tax`, `other` |
| `ConcessionType` | `sibling`, `merit`, `financial_aid`, `staff`, `need_based`, `other` |
| `ConcessionStatus` | `pending`, `approved`, `rejected`, `expired` |
| `OpeningBalanceType` | `invoice`, `payment`, `expense`, `bank`, `other` |

#### `FeeStructure` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `term` | ForeignKey | `to=Term`, `on_delete=PROTECT` |
| `class_name` | CharField | `max_length=64`, `db_index=True` |
| `is_active` | BooleanField | `default=True` |
| `status` | CharField | `max_length=12`, `choices=FeeStructureStatus.choices`, `db_index=True` |
| `locked_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `locked_at` | DateTimeField | `null=True`, `blank=True` |
| `late_pickup_charge` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |
| `sibling_discount_mode` | CharField | `max_length=12`, `choices=SiblingDiscountMode.choices` |
| `sibling_discount_value` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |
| `assessment_fee` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |

**Meta:** `unique_together = ("term", "class_name")`; status lifecycle: Draft → Published → Locked

#### `FeeStructureItem` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `structure` | ForeignKey | `to=FeeStructure`, `on_delete=CASCADE`, `related_name=items` |
| `category` | CharField | `max_length=20`, `choices=FeeCategory.choices` |
| `description` | CharField | `max_length=120` |
| `amount` | DecimalField | `max_digits=12`, `decimal_places=2` |

#### `FinancePeriod` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `name` | CharField | `max_length=50`, `unique=True` |
| `start_date` | DateField | `null=True`, `blank=True` |
| `end_date` | DateField | `null=True`, `blank=True` |
| `is_reconciled` | BooleanField | `default=False` |
| `notes` | TextField | `blank=True` |
| `created_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `reconciled_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `reconciled_at` | DateTimeField | `null=True`, `blank=True` |

**Methods:** `total_invoiced()`, `total_collected()`, `total_expenses()`
**Business rule:** Reconciled periods are read-only for invoices and payments.

#### `OpeningBalance` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `period` | ForeignKey | `to=FinancePeriod`, `on_delete=PROTECT` |
| `balance_type` | CharField | `max_length=12`, `choices=OpeningBalanceType.choices`, `db_index=True` |
| `description` | CharField | `max_length=120` |
| `amount` | DecimalField | `max_digits=14`, `decimal_places=2` |
| `is_debit` | BooleanField | `default=True` |
| `reference` | CharField | `max_length=120`, `blank=True` |
| `notes` | TextField | `blank=True` |
| `created_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |

#### `Invoice` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `null=True`, `blank=True` |
| `applicant` | ForeignKey | `to=Applicant`, `null=True`, `blank=True` |
| `period` | ForeignKey | `to=FinancePeriod`, `null=True`, `blank=True` |
| `term` | ForeignKey | `to=Term`, `null=True`, `blank=True` |
| `invoice_number` | CharField | `max_length=50`, `unique=True`, `null=True`, `blank=True` |
| `amount_due` | DecimalField | `max_digits=12`, `decimal_places=2` |
| `discount_amount` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `total_due` | DecimalField | `max_digits=12`, `decimal_places=2` (auto-calculated) |
| `due_date` | DateField | `null=True`, `blank=True` |
| `status` | CharField | `max_length=20`, `choices=InvoiceStatus.choices` |
| `is_finalized` | BooleanField | `default=False` |
| `last_reminder_at` | DateTimeField | `null=True`, `blank=True` |
| `last_reminder_type` | CharField | `max_length=20`, `blank=True` |

**Business rules:** Must link to student OR applicant. Finalized invoices are immutable. Period-reconciled invoices are read-only.

#### `InvoiceLineItem` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `invoice` | ForeignKey | `to=Invoice`, `on_delete=CASCADE`, `related_name=line_items` |
| `description` | CharField | `max_length=120` |
| `amount` | DecimalField | `max_digits=12`, `decimal_places=2` |
| `is_discount` | BooleanField | `default=False` |

#### `FinanceConfig` (extends TimestampedModel — singleton)
| Field | Type | Options |
|-------|------|---------|
| `school_currency` | CharField | `max_length=10`, `default="TZS"` |
| `fiscal_year_start` | DateField | `null=True`, `blank=True` |
| `fiscal_year_end` | DateField | `null=True`, `blank=True` |
| `invoice_prefix` | CharField | `max_length=10`, `default="INV"` |
| `invoice_due_days` | PositiveIntegerField | `default=30` |
| `invoice_terms` | TextField | `blank=True` |
| `default_payment_method` | CharField | `max_length=20`, `choices=PaymentMethod.choices` |
| `reminder_grace_days` | PositiveIntegerField | `default=7` |
| `auto_reminder_enabled` | BooleanField | `default=False` |
| `finance_officer_email` | EmailField | `blank=True` |
| `late_fee_percentage` | DecimalField | `max_digits=5`, `decimal_places=2`, `default=0` |
| `late_fee_max_days` | PositiveIntegerField | `default=90` |
| `updated_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |

#### `Payment` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `invoice` | ForeignKey | `to=Invoice`, `null=True`, `blank=True` |
| `is_unmatched` | BooleanField | `default=False`, `db_index=True` |
| `amount` | DecimalField | `max_digits=12`, `decimal_places=2` |
| `method` | CharField | `max_length=40`, `choices=PaymentMethod.choices` |
| `payment_date` | DateField | `default=now`, `db_index=True` |
| `reference` | CharField | `max_length=120`, `blank=True` |
| `notes` | TextField | `blank=True` |
| `is_reversal` | BooleanField | `default=False` |
| `reversed_payment` | OneToOneField | `to=self`, `null=True`, `blank=True` |
| `correction_reason` | CharField | `max_length=255`, `blank=True` |
| `created_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |

**Business rules:** Payment records are immutable after creation (save() enforces no-field-change). Deletion raises ValidationError. Reversal reason is mandatory.

#### `UnmatchedPayment` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `amount` | DecimalField | `max_digits=12`, `decimal_places=2` |
| `payment_date` | DateField | `default=now` |
| `method` | CharField | `max_length=40` |
| `reference` | CharField | `max_length=120`, `blank=True` |
| `bank_statement_details` | TextField | `blank=True` |
| `is_resolved` | BooleanField | `default=False` |
| `resolved_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `resolved_at` | DateTimeField | `null=True`, `blank=True` |
| `resolution_invoice` | ForeignKey | `to=Invoice`, `null=True`, `blank=True` |

#### `Budget` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `term` | ForeignKey | `to=Term`, `on_delete=PROTECT` |
| `category` | CharField | `max_length=30`, `choices=ExpenseCategory.choices`, `db_index=True` |
| `allocated_amount` | DecimalField | `max_digits=14`, `decimal_places=2` |
| `notes` | TextField | `blank=True` |
| `is_frozen` | BooleanField | `default=False` |

**Meta:** `unique_together = ("term", "category")`
**Methods:** `spent_amount()`, `remaining_amount()`, `utilization_pct()`

#### `RecurringExpense` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `category` | CharField | `max_length=30`, `choices=ExpenseCategory.choices`, `db_index=True` |
| `amount` | DecimalField | `max_digits=12`, `decimal_places=2` |
| `description` | TextField | |
| `frequency` | CharField | `max_length=12`, `choices=[monthly,termly,yearly]` |
| `vendor` | CharField | `max_length=120`, `blank=True` |
| `reference` | CharField | `max_length=120`, `blank=True` |
| `notes` | TextField | `blank=True` |
| `payment_method` | CharField | `max_length=30`, `choices=PAYMENT_METHODS` |
| `is_active` | BooleanField | `default=True`, `db_index=True` |
| `next_due_date` | DateField | `db_index=True` |
| `last_generated_date` | DateField | `null=True`, `blank=True` |
| `created_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |

**Method:** `process_due(actor)` — creates actual Expense entry and advances due date

#### `Expense` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `category` | CharField | `max_length=30`, `choices=ExpenseCategory.choices`, `db_index=True` |
| `amount` | DecimalField | `max_digits=12`, `decimal_places=2` |
| `description` | TextField | |
| `expense_date` | DateField | `default=now`, `db_index=True` |
| `payment_method` | CharField | `max_length=30` |
| `reference` | CharField | `max_length=120`, `blank=True` |
| `vendor` | CharField | `max_length=120`, `blank=True` |
| `notes` | TextField | `blank=True` |
| `created_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `is_reimbursement` | BooleanField | `default=False` |
| `term` | ForeignKey | `to=Term`, `null=True`, `blank=True` |
| `status` | CharField | `max_length=12`, `choices=ExpenseStatus.choices`, `db_index=True` |
| `approved_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `approved_at` | DateTimeField | `null=True`, `blank=True` |
| `rejection_reason` | TextField | `blank=True` |

**Business rule:** Expense amount must be > 0.

#### `ReminderConfiguration` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `is_active` | BooleanField | `default=True` |
| `reminder_frequency` | CharField | `max_length=20`, `choices=[daily,weekly,every_3_days,custom]` |
| `reminder_type` | CharField | `max_length=10`, `choices=[email,sms,both]` |
| `first_reminder_days` | PositiveIntegerField | `default=7` |
| `second_reminder_days` | PositiveIntegerField | `default=14` |
| `final_reminder_days` | PositiveIntegerField | `default=30` |
| `reminder_template_sms` | TextField | `blank=True` |
| `reminder_template_email` | TextField | `blank=True` |
| `auto_escalate_to_hos` | BooleanField | `default=False` |
| `escalation_days` | PositiveIntegerField | `default=45` |
| `notify_finance_officer` | BooleanField | `default=True` |
| `updated_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `last_processed_at` | DateTimeField | `null=True`, `blank=True` |

#### `Concession` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=PROTECT` |
| `term` | ForeignKey | `to=Term`, `on_delete=PROTECT` |
| `concession_type` | CharField | `max_length=20`, `choices=ConcessionType.choices`, `db_index=True` |
| `reason` | CharField | `max_length=255`, `blank=True` |
| `discount_mode` | CharField | `max_length=12`, `choices=SiblingDiscountMode.choices` |
| `discount_value` | DecimalField | `max_digits=10`, `decimal_places=2` |
| `status` | CharField | `max_length=12`, `choices=ConcessionStatus.choices`, `db_index=True` |
| `approved_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `approved_at` | DateTimeField | `null=True`, `blank=True` |
| `rejection_reason` | TextField | `blank=True` |
| `created_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `notes` | TextField | `blank=True` |
| `valid_from` | DateField | `null=True`, `blank=True` |
| `valid_until` | DateField | `null=True`, `blank=True` |

---

### 2.12 hr/models.py (14 models + 8 enums)

#### Enums
| Name | Values |
|------|--------|
| `StaffCategory` | `teaching`, `non_teaching` |
| `EmploymentType` | `permanent`, `contract`, `temporary`, `probation`, `intern` |
| `Gender` | `male`, `female`, `other` |
| `MaritalStatus` | `single`, `married`, `divorced`, `widowed` |
| `PayrollStatus` | `draft`, `pending_approval`, `approved`, `paid`, `locked` |
| `PayslipStatus` | `draft`, `confirmed`, `paid` |
| `LeaveType` | `annual`, `sick`, `maternity`, `paternity`, `study`, `compassionate`, `unpaid`, `other` |
| `LeaveStatus` | `pending`, `approved`, `rejected`, `cancelled` |
| `OffboardingStatus` | `initiated`, `in_progress`, `completed` |

#### `StaffProfile` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `user` | OneToOneField | `to=User`, `on_delete=CASCADE`, `related_name=staff_profile` |
| `full_name` | CharField | `max_length=150` |
| `date_of_birth` | DateField | `null=True`, `blank=True` |
| `gender` | CharField | `max_length=10`, `choices=Gender.choices`, `blank=True` |
| `nationality` | CharField | `max_length=50`, `blank=True` |
| `marital_status` | CharField | `max_length=12`, `choices=MaritalStatus.choices`, `blank=True` |
| `residential_address` | TextField | `blank=True` |
| `emergency_contact_name` | CharField | `max_length=150`, `blank=True` |
| `emergency_contact_phone` | CharField | `max_length=32`, `blank=True` |
| `emergency_contact_relationship` | CharField | `max_length=50`, `blank=True` |
| `employee_id` | CharField | `max_length=32`, `unique=True`, `null=True`, `blank=True` |
| `job_title` | CharField | `max_length=100` |
| `department` | CharField | `max_length=32`, `choices=Department.choices`, `blank=True` |
| `departments` | JSONField | `default=list`, `blank=True` |
| `staff_category` | CharField | `max_length=20`, `choices=StaffCategory.choices` |
| `employment_type` | CharField | `max_length=12`, `choices=EmploymentType.choices` |
| `employment_start_date` | DateField | |
| `probation_end_date` | DateField | `null=True`, `blank=True` |
| `confirmation_date` | DateField | `null=True`, `blank=True` |
| `contract_end_date` | DateField | `null=True`, `blank=True` |
| `years_of_experience` | PositiveIntegerField | `default=0` |
| `highest_qualification` | CharField | `max_length=100`, `blank=True` |
| `contact_phone` | CharField | `max_length=32`, `blank=True` |
| `contact_email` | EmailField | |
| `basic_salary` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `housing_allowance` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `transport_allowance` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `medical_allowance` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `other_allowances` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `bank_name` | CharField | `max_length=100`, `blank=True` |
| `bank_account_number` | CharField | `max_length=50`, `blank=True` |
| `bank_branch` | CharField | `max_length=100`, `blank=True` |
| `national_id_number` | CharField | `max_length=50`, `blank=True` |
| `nssf_number` | CharField | `max_length=50`, `blank=True` |
| `tin_number` | CharField | `max_length=50`, `blank=True` |
| `paye_code` | CharField | `max_length=20`, `blank=True` |
| `heslb_loan_number` | CharField | `max_length=50`, `blank=True` |
| `heslb_has_loan` | BooleanField | `default=False` |
| `heslb_deduction_type` | CharField | `max_length=12`, `choices=[fixed,percentage]`, `blank=True` |
| `heslb_deduction_value` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |
| `heslb_deduction_order_ref` | CharField | `max_length=100`, `blank=True` |
| `heslb_deduction_start_date` | DateField | `null=True`, `blank=True` |
| `nhif_number` | CharField | `max_length=50`, `blank=True` |
| `onboarding_step` | PositiveSmallIntegerField | `default=0` |
| `onboarding_completed` | BooleanField | `default=False` |
| `departure_date` | DateField | `null=True`, `blank=True` |
| `departure_reason` | TextField | `blank=True` |
| `is_active` | BooleanField | `default=True` |

**Properties:** `gross_salary()`, `system_role`, `is_teaching_staff`, `contract_days_remaining`, `contract_expiring_soon`, `total_monthly_allowances`
**Methods:** `nssf_employee_contribution()`, `nssf_employer_contribution()`, `nhif_contribution()`

#### `StaffDocument` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `staff` | ForeignKey | `to=StaffProfile`, `on_delete=CASCADE`, `related_name=documents` |
| `name` | CharField | `max_length=150` |
| `file` | FileField | `upload_to="staff/documents/"` |
| `document_type` | CharField | `max_length=30`, `choices=[id_card,certification,degree,contract,payslip,nssf,nhif,tin,other]` |
| `notes` | TextField | `blank=True` |
| `uploaded_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |

#### `TeacherClassAssignment` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `teacher` | ForeignKey | `to=StaffProfile`, `on_delete=CASCADE` |
| `term` | ForeignKey | `to=Term`, `on_delete=CASCADE` |
| `grade_class` | ForeignKey | `to=GradeClass`, `on_delete=CASCADE` |
| `is_class_teacher` | BooleanField | `default=False` |
| `subjects_taught` | JSONField | `default=list` |

**Meta:** `unique_together = ("teacher", "term", "grade_class")`
**Business rules:** Cross-department assignment blocked. One class teacher per teacher per term.

#### `OnboardingChecklistItem` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `step` | PositiveSmallIntegerField | |
| `item_name` | CharField | `max_length=200` |
| `description` | TextField | `blank=True` |
| `is_required` | BooleanField | `default=True` |
| `order` | PositiveSmallIntegerField | `default=0` |

**Meta:** `ordering = ["step", "order"]`

#### `StaffOnboardingProgress` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `staff` | OneToOneField | `to=StaffProfile`, `on_delete=CASCADE` |
| `current_step` | PositiveSmallIntegerField | `default=1` |
| `is_completed` | BooleanField | `default=False` |
| `completed_at` | DateTimeField | `null=True`, `blank=True` |
| `documents_submitted` | BooleanField | `default=False` |
| `contract_signed` | BooleanField | `default=False` |
| `id_verified` | BooleanField | `default=False` |
| `bank_details_provided` | BooleanField | `default=False` |
| `statutory_registered` | BooleanField | `default=False` |
| `system_account_created` | BooleanField | `default=False` |
| `induction_completed` | BooleanField | `default=False` |
| `probation_passed` | BooleanField | `default=False` |
| `orientation_completed` | BooleanField | `default=False` |
| `emergency_contacts_provided` | BooleanField | `default=False` |
| `completed_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |

#### `InductionChecklistCompletion` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `staff` | ForeignKey | `to=StaffProfile`, `on_delete=CASCADE` |
| `checklist_item` | ForeignKey | `to=OnboardingChecklistItem`, `on_delete=CASCADE` |
| `is_completed` | BooleanField | `default=False` |
| `completed_at` | DateTimeField | `null=True`, `blank=True` |
| `completed_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |

**Meta:** `unique_together = ("staff", "checklist_item")`

#### `PayrollRun` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `period_name` | CharField | `max_length=64` |
| `payroll_type` | CharField | `max_length=20`, `choices=[monthly,bonus,termination,special]` |
| `term` | ForeignKey | `to=Term`, `null=True`, `blank=True` |
| `status` | CharField | `max_length=20`, `choices=PayrollStatus.choices`, `db_index=True` |
| `period_start` | DateField | `null=True`, `blank=True` |
| `period_end` | DateField | `null=True`, `blank=True` |
| `payment_date` | DateField | `null=True`, `blank=True` |
| `total_gross_pay` | DecimalField | `max_digits=14`, `decimal_places=2`, `default=0` |
| `total_deductions` | DecimalField | `max_digits=14`, `decimal_places=2`, `default=0` |
| `total_net_pay` | DecimalField | `max_digits=14`, `decimal_places=2`, `default=0` |
| `employee_count` | PositiveIntegerField | `default=0` |
| `approved_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `approved_at` | DateTimeField | `null=True`, `blank=True` |
| `processed_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |

**Method:** `calculate_summary()` — recalculates totals from payroll entries

#### `PayrollEntry` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `payroll_run` | ForeignKey | `to=PayrollRun`, `on_delete=CASCADE` |
| `staff` | ForeignKey | `to=StaffProfile`, `on_delete=PROTECT` |
| `basic_pay` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `housing_allowance` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `transport_allowance` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `medical_allowance` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `other_allowances` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `overtime_pay` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |
| `bonus_pay` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |
| `paye_tax` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |
| `nssf_employee` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |
| `nssf_employer` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |
| `nhif_deduction` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |
| `hesb_deduction` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |
| `other_deductions` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |
| `deduction_notes` | TextField | `blank=True` |
| `unpaid_leave_days` | PositiveSmallIntegerField | `default=0` |
| `leave_deduction` | DecimalField | `max_digits=10`, `decimal_places=2`, `default=0` |
| `gross_pay` | DecimalField | `max_digits=12`, `decimal_places=2`, `editable=False` |
| `total_deductions` | DecimalField | `max_digits=12`, `decimal_places=2`, `editable=False` |
| `net_pay` | DecimalField | `max_digits=12`, `decimal_places=2`, `editable=False` |
| `status` | CharField | `max_length=12`, `choices=PayslipStatus.choices` |
| `payslip_generated` | BooleanField | `default=False` |
| `paid_at` | DateTimeField | `null=True`, `blank=True` |

**Meta:** `unique_together = ("payroll_run", "staff")`
**Methods:** `calculate()`, `auto_calculate_from_profile()` — populates from StaffProfile + PAYE bands + statutory config

#### `StatutoryFiling` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `filing_type` | CharField | `max_length=10`, `choices=[nssf,paye,heslb,nhif,wcf,sdl]`, `db_index=True` |
| `payroll_run` | ForeignKey | `to=PayrollRun`, `on_delete=CASCADE` |
| `period_name` | CharField | `max_length=64` |
| `employee_contribution` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `employer_contribution` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `total_amount` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `status` | CharField | `max_length=12`, `choices=StatutoryFilingStatus.choices` |
| `due_date` | DateField | `null=True`, `blank=True` |
| `submitted_date` | DateField | `null=True`, `blank=True` |
| `reference_number` | CharField | `max_length=100`, `blank=True` |
| `submitted_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `notes` | TextField | `blank=True` |

**Meta:** `unique_together = ("filing_type", "payroll_run")`

#### `PayrollConfig` (extends TimeStampedModel — singleton)
| Field | Type | Options |
|-------|------|---------|
| `nssf_employee_rate` | DecimalField | `max_digits=5`, `decimal_places=2`, `default=10.00` |
| `nssf_employer_rate` | DecimalField | `max_digits=5`, `decimal_places=2`, `default=10.00` |
| `working_days_per_month` | PositiveIntegerField | `default=26` |
| `updated_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |

#### `PAYETaxBand` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `version` | CharField | `max_length=20`, `default="2024/2025"` |
| `band_from` | DecimalField | `max_digits=12`, `decimal_places=2` |
| `band_to` | DecimalField | `max_digits=12`, `decimal_places=2`, `null=True`, `blank=True` |
| `base_tax` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `rate_percentage` | DecimalField | `max_digits=5`, `decimal_places=2` |
| `is_active` | BooleanField | `default=True` |
| `effective_from` | DateField | `null=True`, `blank=True` |

**Meta:** `ordering = ["band_from"]`

#### `LeaveAllocation` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `staff` | ForeignKey | `to=StaffProfile`, `on_delete=CASCADE` |
| `year` | PositiveSmallIntegerField | |
| `leave_type` | CharField | `max_length=15`, `choices=LeaveType.choices` |
| `total_days` | PositiveSmallIntegerField | `default=0` |
| `used_days` | PositiveSmallIntegerField | `default=0` |
| `pending_days` | PositiveSmallIntegerField | `default=0` |

**Meta:** `unique_together = ("staff", "year", "leave_type")`
**Property:** `remaining_days`

#### `LeaveRequest` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `staff` | ForeignKey | `to=StaffProfile`, `on_delete=CASCADE` |
| `leave_type` | CharField | `max_length=15`, `choices=LeaveType.choices` |
| `start_date` | DateField | |
| `end_date` | DateField | |
| `total_days` | PositiveSmallIntegerField | `editable=False` |
| `reason` | TextField | `blank=True` |
| `status` | CharField | `max_length=12`, `choices=LeaveStatus.choices`, `db_index=True` |
| `approved_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `approved_at` | DateTimeField | `null=True`, `blank=True` |
| `rejection_reason` | TextField | `blank=True` |
| `contact_during_leave` | CharField | `max_length=50`, `blank=True` |
| `handover_notes` | TextField | `blank=True` |
| `supporting_document` | FileField | `upload_to="staff/leave_docs/"`, `null=True`, `blank=True` |

#### `OffboardingRecord` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `staff` | OneToOneField | `to=StaffProfile`, `on_delete=CASCADE` |
| `status` | CharField | `max_length=15`, `choices=OffboardingStatus.choices` |
| `resignation_received` | BooleanField | `default=False` |
| `clearance_assets_returned` | BooleanField | `default=False` |
| `clearance_library` | BooleanField | `default=False` |
| `clearance_finance` | BooleanField | `default=False` |
| `final_payslip_generated` | BooleanField | `default=False` |
| `exit_interview_completed` | BooleanField | `default=False` |
| `system_account_deactivated` | BooleanField | `default=False` |
| `notice_period_days` | PositiveSmallIntegerField | `default=30` |
| `last_working_day` | DateField | `null=True`, `blank=True` |
| `final_settlement_amount` | DecimalField | `max_digits=12`, `decimal_places=2`, `default=0` |
| `settlement_paid` | BooleanField | `default=False` |
| `settlement_paid_date` | DateField | `null=True`, `blank=True` |
| `exit_interview_date` | DateField | `null=True`, `blank=True` |
| `exit_reason` | TextField | `blank=True` |
| `feedback_notes` | TextField | `blank=True` |
| `initiated_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `completed_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `completed_at` | DateTimeField | `null=True`, `blank=True` |
| `is_rehirable` | BooleanField | `default=True` |

**Properties:** `clearance_completed`, `all_steps_completed`

---

### 2.13 ptc/models.py (8 models + 4 enums)

#### Enums
| Name | Values |
|------|--------|
| `TermSlot` | `term_1`, `term_3` |
| `EnrichmentLetterGrade` | `A*`, `A`, `B`, `C`, `D`, `E` |
| `LearnerAttributeRating` | `E` (Excellent), `G` (Good), `S` (Satisfactory), `N` (Needs Improvement) |

#### `PTCWindow` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `academic_year` | ForeignKey | `to=AcademicYear`, `on_delete=PROTECT` |
| `term_slot` | CharField | `max_length=10`, `choices=TermSlot.choices` |
| `ptc_date` | DateField | |
| `notification_window_days` | PositiveIntegerField | `default=30` |
| `notification_window_opens_at` | DateField | `null=True`, `editable=False` |
| `notification_window_closes_at` | DateField | `null=True`, `editable=False` |
| `comment_entry_window_days` | PositiveIntegerField | `default=14` |
| `comment_entry_window_opens_at` | DateField | `null=True`, `editable=False` |
| `comment_entry_window_closes_at` | DateField | `null=True`, `editable=False` |
| `is_published` | BooleanField | `default=False`, `db_index=True` |
| `created_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `last_modified_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |

**Meta:** `unique_together = ("academic_year", "term_slot")`; Two windows per year: Term 1 and Term 3 (FR-PTC-001)

#### `EnrichmentSubject` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `name` | CharField | `max_length=40`, `choices=ENRICHMENT_SUBJECT_CHOICES`, `unique=True` |
| `assigned_teacher` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `assigned_class` | ForeignKey | `to=GradeClass`, `null=True`, `blank=True` |
| `is_active` | BooleanField | `default=True` |

**Fixed subjects:** Bible, ICT, Global Perspective, PE, Swimming, French, Music (7 total — FR-PTC-006)

#### `EnrichmentGrade` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=PROTECT` |
| `enrichment_subject` | ForeignKey | `to=EnrichmentSubject`, `on_delete=PROTECT` |
| `term` | ForeignKey | `to=Term`, `on_delete=PROTECT` |
| `letter_grade` | CharField | `max_length=2`, `choices=EnrichmentLetterGrade.choices`, `blank=True` |
| `entered_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `is_locked` | BooleanField | `default=False` |

**Meta:** `unique_together = ("student", "enrichment_subject", "term")`

#### `PTCSubjectComment` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=PROTECT` |
| `ptc_window` | ForeignKey | `to=PTCWindow`, `on_delete=PROTECT` |
| `subject_name` | CharField | `max_length=80`, `db_index=True` |
| `comment_text` | TextField | `blank=True` |
| `entered_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |

**Meta:** `unique_together = ("student", "ptc_window", "subject_name")`

#### `LearnerAttributeRatingEntry` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=PROTECT` |
| `ptc_window` | ForeignKey | `to=PTCWindow`, `on_delete=PROTECT` |
| `attribute_number` | PositiveSmallIntegerField | `choices=1-12` (fixed attributes per FR-PTC-019) |
| `rating` | CharField | `max_length=1`, `choices=LearnerAttributeRating.choices`, `blank=True` |
| `entered_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |

**Meta:** `unique_together = ("student", "ptc_window", "attribute_number")`

#### `PTCGenerationLog` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=PROTECT` |
| `class_teacher` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `ptc_window` | ForeignKey | `to=PTCWindow`, `null=True`, `blank=True` |
| `opened_at` | DateTimeField | `auto_now_add=True` |

#### `PTCProgressTrendOverride` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=PROTECT` |
| `ptc_window` | ForeignKey | `to=PTCWindow`, `on_delete=PROTECT` |
| `overridden_trend` | CharField | `max_length=20`, `choices=[improving,stable,needs_attention]` |
| `entered_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |

**Meta:** `unique_together = ("student", "ptc_window")`

#### `PTCDateChangeLog` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `ptc_window` | ForeignKey | `to=PTCWindow`, `on_delete=CASCADE` |
| `changed_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `old_ptc_date` | DateField | |
| `new_ptc_date` | DateField | |
| `changed_at` | DateTimeField | `auto_now_add=True` |

---

### 2.14 tasks/models.py (5 models)

#### `Task` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `id` | UUIDField | `primary_key=True`, `default=uuid4` |
| `task_type` | CharField | `max_length=50`, `choices=TASK_TYPE_CHOICES` |
| `title` | CharField | `max_length=200` |
| `description` | TextField | `blank=True` |
| `assigned_to` | ForeignKey | `to=User`, `on_delete=CASCADE` |
| `created_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `due_date` | DateTimeField | |
| `completed_at` | DateTimeField | `null=True`, `editable=False` |
| `status` | CharField | `max_length=20`, `choices=[pending,in_progress,completed,deferred,cancelled]` |
| `priority` | CharField | `max_length=20`, `choices=[critical,high,medium,low]` |
| `is_overdue` | BooleanField | `default=False` |
| `content_type` | ForeignKey | `to=ContentType`, `null=True`, `blank=True` |
| `object_id` | UUIDField | `null=True`, `blank=True` |
| `metadata` | JSONField | `default=dict` |
| `actions_available` | JSONField | `default=list` |
| `notification_sent` | BooleanField | `default=False` |
| `reminder_sent` | BooleanField | `default=False` |
| `internal_notes` | TextField | `blank=True` |

**Methods:** `mark_completed()`, `mark_in_progress()`, `defer_until()`, `update_overdue_status()`

#### `TaskHistory` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `id` | UUIDField | `primary_key=True` |
| `task` | ForeignKey | `to=Task`, `on_delete=CASCADE` |
| `action` | CharField | `max_length=50` |
| `old_value` | TextField | `null=True`, `blank=True` |
| `new_value` | TextField | `null=True`, `blank=True` |
| `changed_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `comment` | TextField | `blank=True` |

#### `TaskComment` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `id` | UUIDField | `primary_key=True` |
| `task` | ForeignKey | `to=Task`, `on_delete=CASCADE` |
| `author` | ForeignKey | `to=User`, `null=True` |
| `content` | TextField | |
| `is_internal` | BooleanField | `default=False` |

#### `TaskTemplate` (non-timestamped model)
| Field | Type | Options |
|-------|------|---------|
| `id` | UUIDField | `primary_key=True` |
| `name` | CharField | `max_length=100` |
| `task_type` | CharField | `max_length=50`, `choices=TASK_TYPE_CHOICES` |
| `title_template` | CharField | `max_length=200` |
| `description` | TextField | `blank=True` |
| `priority` | CharField | `max_length=20`, `choices=PRIORITY_CHOICES` |
| `actions_available` | JSONField | `default=list` |
| `metadata_template` | JSONField | `default=dict` |
| `days_until_due` | PositiveIntegerField | `default=2` |
| `is_active` | BooleanField | `default=True` |
| `created_at` | DateTimeField | `auto_now_add=True` |

#### `TaskNotificationLog` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `id` | UUIDField | `primary_key=True` |
| `task` | ForeignKey | `to=Task`, `on_delete=CASCADE` |
| `notification_type` | CharField | `max_length=50` |
| `sent_to` | ForeignKey | `to=User`, `on_delete=CASCADE` |
| `channel` | CharField | `max_length=50`, `choices=[in_app,email,push,sms]` |
| `status` | CharField | `max_length=20`, `default="pending"` |
| `error_message` | TextField | `blank=True` |

---

### 2.15 timetable/models.py (2 models + 1 enum)

#### Enum
| Name | Values |
|------|--------|
| `Weekday` | `mon`, `tue`, `wed`, `thu`, `fri` |

#### `TimetableSlot` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `term` | ForeignKey | `to=Term`, `on_delete=PROTECT`, `null=True` |
| `class_name` | CharField | `max_length=64`, `db_index=True` |
| `subject` | ForeignKey | `to=Subject`, `on_delete=PROTECT`, `null=True` |
| `subject_name` | CharField | `max_length=80` |
| `teacher` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `day_of_week` | CharField | `max_length=8`, `choices=Weekday.choices`, `db_index=True` |
| `start_time` | TimeField | |
| `end_time` | TimeField | |
| `room` | ForeignKey | `to=Room`, `on_delete=SET_NULL`, `null=True`, `blank=True` |

**Indexes:** on `(term, class_name, day_of_week, start_time)`, `(term, teacher, day_of_week, start_time)`
**Validation:** Cross-department restriction, subject-in-assignment check, double-booking protection

#### `EcdBreakConfig` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `class_name` | CharField | `max_length=64`, `unique=True` |
| `snack_break_start` | TimeField | |
| `snack_break_end` | TimeField | |
| `lunch_break_start` | TimeField | |
| `lunch_break_end` | TimeField | |
| `nap_time_start` | TimeField | `null=True`, `blank=True` |
| `nap_time_end` | TimeField | `null=True`, `blank=True` |

---

### 2.16 welfare/models.py (2 models + 2 enums)

#### Enums
| Name | Values |
|------|--------|
| `WelfareSeverity` | `low`, `medium`, `high`, `critical` |
| `WelfareConcernType` | `behavioral`, `health`, `attendance`, `academic`, `home_situation`, `other` |

#### `WelfareAcknowledgment` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `observation` | ForeignKey | `to=WelfareObservation`, `on_delete=CASCADE` |
| `user` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `acknowledged_at` | DateTimeField | `auto_now_add=True` |

**Meta:** `unique_together = ("observation", "user")`

#### `WelfareObservation` (extends TimeStampedModel)
| Field | Type | Options |
|-------|------|---------|
| `student` | ForeignKey | `to=Student`, `on_delete=PROTECT` |
| `submitted_by` | ForeignKey | `to=User`, `on_delete=PROTECT` |
| `concern_type` | CharField | `max_length=20`, `choices=WelfareConcernType.choices`, `db_index=True` |
| `severity` | CharField | `max_length=10`, `choices=WelfareSeverity.choices`, `db_index=True` |
| `observation_date` | DateField | `db_index=True` |
| `observation_text` | TextField | |
| `action_taken` | TextField | `blank=True` |
| `parent_contacted` | BooleanField | `default=False` |
| `parent_contact_datetime` | DateTimeField | `null=True`, `blank=True` |
| `parent_confirmed` | BooleanField | `default=False` |
| `parent_confirmation_date` | DateTimeField | `null=True`, `blank=True` |
| `follow_up_required` | BooleanField | `default=False` |
| `follow_up_date` | DateField | `null=True`, `blank=True` |
| `is_locked` | BooleanField | `default=False` |
| `hod_status` | CharField | `max_length=20`, `choices=[pending,in_progress,resolved]`, `blank=True` |
| `hod_notes` | TextField | `blank=True` |
| `reviewed_by` | ForeignKey | `to=User`, `null=True`, `blank=True` |
| `reviewed_at` | DateTimeField | `null=True`, `blank=True` |
| `parent_signed` | BooleanField | `default=False` |
| `parent_signed_at` | DateTimeField | `null=True`, `blank=True` |
| `parent_signature_name` | CharField | `max_length=255`, `blank=True` |

**Meta:** indexes on `(student, observation_date)`, `(severity, hod_status)`
**Methods:** `is_hod_acknowledged()`, `is_hos_acknowledged()`, `is_fully_acknowledged()`

## 3. ALL URL PATTERNS

### 3.1 Main router (config/urls.py  + app_name/urls.py)

| Prefix | App | Patterns |
|--------|-----|----------|
| `/` | core | `""` → dashboard, `settings/`, `seed-db/`, `api/session/check/`, `api/session/extend/` |
| `/users/` | users | `login/`, `logout/`, `list/`, `create/`, `edit/<pk>/`, `profile/`, `password-change/`, `impersonate/*`, `password-reset/*` |
| `/academics/` | academics | 55 patterns — lesson plans, exam scores, terms, years, rooms, classes, subjects, ECD, reports, progression, analytics |
| `/students/` | students | student CRUD, detail, guardian management |
| `/admissions/` | admissions | pipeline, inquiry, assessment, waitlist, offer letters |
| `/attendance/` | attendance | today, staff, parent, PWA, API endpoints |
| `/timetable/` | timetable | index, slot CRUD, break config |
| `/finance/` | finance | invoices, payments, expenses, budgets, fee structures, statements, reports |
| `/hr/` | hr | staff, payroll, leave, onboarding, offboarding, statutory filing |
| `/communications/` | communications | broadcast, inbox, weekly focus |
| `/discipline/` | discipline | incidents, hearings, sanctions |
| `/welfare/` | welfare | observations, dashboard |
| `/events/` | events | calendar, event CRUD |
| `/parent-portal/` | parent_portal | dashboard, attendance, grades, invoices, communications |
| `/ptc/` | ptc | PTC screen, attribute entry, enrichment, comments, compliance |
| `/audit/` | audit | logs, DSAR export |
| `/reports/` | reports | dashboard |
| `/tasks/` | tasks | dashboard |

---

## 4. ALL VIEWS (by app, with line numbers)

### core/views.py (1876 lines)
| Line | View | Type |
|------|------|------|
| 152 | `DashboardRouterView` | TemplateView |
| 212 | `SuperAdminDashboardView` | TemplateView |
| 256 | `HOSDashboardView` | TemplateView |
| 707 | `PrimaryHODDashboardView` | TemplateView |
| 990 | `ECDHODDashboardView` | TemplateView |
| 1149 | `AdminDashboardView` | TemplateView |
| 1244 | `FinanceDashboardProxyView` | TemplateView |
| 1251 | `TeacherDashboardView` | TemplateView |
| 1425 | `ParentDashboardView` | TemplateView |
| 1460 | `SchoolSettingsUpdateView` | View |
| 1836 | `SessionCheckView` | View |
| 1861 | `SessionExtendView` | View |

### users/views.py (912 lines)
| Line | View | Type |
|------|------|------|
| 29 | `HISMSLoginView` | LoginView |
| 165 | `StartImpersonationView` | View |
| 194 | `StopImpersonationView` | View |
| 221 | `UserListView` | ListView |
| 308 | `UserProfileView` | TemplateView |
| 369 | `HISMSPasswordChangeView` | PasswordChangeView |
| 386 | `HISMSPasswordChangeDoneView` | PasswordChangeDoneView |
| 442 | `UserCreateView` | CreateView |
| 469 | `UserUpdateView` | UpdateView |
| 679 | `HISMSLogoutView` | View |
| 756 | `PasswordResetRequestView` | FormView |
| 821 | `PasswordResetDoneView` | TemplateView |
| 829 | `PasswordResetConfirmView` | FormView |
| 907 | `PasswordResetCompleteView` | TemplateView |

### academics/views.py (5964 lines)
| Line | View | Type |
|------|------|------|
| 68 | `LessonPlanListView` | TemplateView |
| 177 | `LessonPlanCreateView` | CreateView |
| 203 | `LessonPlanUpdateView` | UpdateView |
| 448 | `LessonPlanSubmitView` | View |
| 495 | `LessonPlanWithdrawView` | View |
| 532 | `LessonPlanAttachmentDeleteView` | View |
| 762 | `LessonPlanReviewView` | View |
| 819 | `LessonPlanComplianceView` | TemplateView |
| 984 | `ExamScoreEntryView` | TemplateView |
| 1277 | `ReportRouterView` | View |
| 1292 | `HOSSignOffListView` | TemplateView |
| 1363 | `MigrationTriggerView` | View |
| 1379 | `ReportCardListView` | TemplateView |
| 1498 | `HOSReportPreviewView` | TemplateView |
| 1522 | `HOSSignOffActionView` | View |
| 1569 | `ReportReviewQueueView` | TemplateView |
| 1710 | `AcademicAnalyticsView` | TemplateView |
| 2130 | `ExamScoreCorrectionView` | View |
| 2170 | `ExamScoreApprovalQueueView` | TemplateView |
| 2322 | `AtRiskStudentsListView` | TemplateView |
| 2416 | `AcademicYearListView` | ListView |
| 2427 | `AcademicYearCreateView` | CreateView |
| 2435 | `TermListView` | ListView |
| 2441 | `TermCreateView` | CreateView |
| 2450 | `ParentReportListView` | TemplateView |
| 2474 | `ECDEvaluationEntryView` | TemplateView |
| 2822 | `RoomListView` | View |
| 2856 | `RoomEditView` | View |
| 2886 | `RoomDeleteView` | View |
| 2941 | `DuplicateCheckView` | View |
| 3002 | `CambridgeCheckpointEntryView` | TemplateView |
| 3133 | `AcademicReportExportView` | View |
| 3201 | `ECDStudentsAPIView` | View |
| 3229 | `ECDEvaluationAPIView` | View |
| 3502 | `ECDSubmissionAPIView` | View |
| 3596 | `ECDAssessmentView` | TemplateView |
| 3776 | `ECDProgressAPIView` | View |
| 3830 | `PrimaryCommentEntryView` | TemplateView |
| 3932 | `ScoreCorrectionHistoryView` | TemplateView |
| 3952 | `SeedECDView` | View |
| 4010 | `PrimaryScoreEntryView` | TemplateView |
| 4079 | `PrimaryStudentsAPIView` | View |
| 4098 | `PrimaryScoreAPIView` | View |
| 4268 | `PrimaryBulkSubmissionAPIView` | View |
| 4365 | `GradeClassListView` | View |
| 4397 | `GradeClassEditView` | View |
| 4424 | `GradeClassDeleteView` | View |
| 4440 | `SubjectListView` | TemplateView |
| 4475 | `SubjectEditView` | UpdateView |
| 4497 | `SubjectDeleteView` | View |
| 4510 | `ReportPDFDownloadView` | View |
| 4562 | `StudentAcademicRecordView` | TemplateView |
| 4860 | `ProgressionConfigListView` | View |
| 4942 | `ProgressionConfigEditView` | View |
| 4982 | `ProgressionConfigDeleteView` | View |
| 5004 | `ProgressionCaseCalculateView` | View |
| 5064 | `ProgressionHODReviewListView` | TemplateView |
| 5140 | `ProgressionHODReviewActionView` | View |
| 5227 | `ProgressionHODBulkApproveView` | View |
| 5285 | `ProgressionHOSDecisionListView` | TemplateView |
| 5350 | `ProgressionHOSDecisionActionView` | View |
| 5424 | `ProgressionHOSBulkDecideView` | View |
| 5476 | `ProgressionTeacherView` | TemplateView |
| 5534 | `ProgressionParentView` | TemplateView |
| 5702 | `ProgressionBulkPromotionView` | TemplateView |
| 5772 | `ProgressionExecutePromotionView` | View |

---

## 5. ALL FORMS

| App | Form | Fields |
|-----|------|--------|
| core | `SchoolSettingsForm` | school_name, attendance thresholds, SMS/email/WhatsApp config, PWA settings |
| users | `UserCreateForm` | username, first_name, last_name, email, role, password, is_staff, is_superuser, profile_picture, groups, permissions |
| users | `UserUpdateForm` | same + new_password, assignment fields |
| academics | `LessonPlanForm` | term, class_name, subject_name, week_start_date |
| academics | `LessonPlanReviewForm` | decision, feedback |
| academics | `ExamScoreFilterForm` | term, class_name, subject_name, exam_type |
| academics | `SubjectForm` | name, code, color, departments, classes, is_active |
| academics | `TermForm` | academic_year, name, start_date, end_date, grading_deadline, is_locked |

---

## 6. ALL TEMPLATES (167 HTML files)

| Directory | Count | Templates |
|-----------|-------|-----------|
| `templates/` (root) | 6 | `base.html`, `dashboard.html`, `400.html`, `403.html`, `404.html`, `500.html` |
| `templates/_partials/` | 1 | `form_field.html` |
| `templates/academics/` | 38 | lesson plans, exams, terms, years, rooms, classes, subjects, ECD, reports, progression, analytics |
| `templates/admissions/` | 15 | pipeline, inquiry, assessment, waitlist, offer letters |
| `templates/attendance/` | 10 | today, staff, parent, PWA, modals |
| `templates/audit/` | 3 | logs, DSAR export |
| `templates/communications/` | 7 | broadcast, inbox, weekly focus |
| `templates/core/` | 1 | `settings.html` |
| `templates/dashboards/` | 8 | superadmin, hos, primary_hod, ecd_hod, admin, teacher, parent, seed_db |
| `templates/discipline/` | 4 | incidents, hearings, queue |
| `templates/events/` | 3 | calendar, form |
| `templates/finance/` | 40 | invoices, payments, expenses, budgets, fee structures, reports, statements |
| `templates/hr/` | 26 | staff, payroll, leave, onboarding, offboarding |
| `templates/parent_portal/` | 6 | dashboard, attendance, grades, invoices, communications, child_detail |
| `templates/ptc/` | 9 | PTC screen, attributes, enrichment, comments, compliance |
| `templates/registration/` | 9 | login, password change/reset, activation email |
| `templates/reports/` | 1 | dashboard |
| `templates/students/` | 9 | list, form, detail, guardian CRUD, ID card |
| `templates/tasks/` | 1 | dashboard |
| `templates/timetable/` | 6 | index, slots, break config |
| `templates/users/` | 3 | profile, user_form, user_list |
| `templates/welfare/` | 6 | observations, dashboard, incidents |
| `includes/` | 1 | `hodari_print_styles.html` |

---

## 7. MANAGEMENT COMMANDS

| App | Command | Purpose |
|-----|---------|---------|
| academics | `promote_students` | CLI year-end promotion (legacy fallback) |
| academics | `seed_ecd_students` | Seed ECD test data |
| academics | `check_and_advance_term` | Term auto-advancement |
| academics | `mark_missing_lesson_plans` | Flag missing lesson plans |

---

## 8. DATABASE STRUCTURE (summary by app)

| App | Tables | Key Indexes | Key Constraints |
|-----|--------|-------------|-----------------|
| core | `core_mediasettings`, `core_schoolsettings` | area unique | singleton on schoolsettings |
| users | `users_user`, `users_user_groups`, `users_user_user_permissions` | role idx | custom auth + role-based permissions |
| academics | `academics_academicyear`, `academics_term`, `academics_gradeclass`, `academics_subject`, `academics_subject_classes`, `academics_timetableentry`, `academics_lessonplan`, `academics_lessonplanattachment`, `academics_examtypeconfiguration`, `academics_ecdtemplateconfiguration`, `academics_examscore`, `academics_reportcard`, `academics_ecdevaluation`, `academics_cambridgecheckpointscore`, `academics_abcpaceprogress`, `academics_abcscripture`, `academics_abcreadingprogramme`, `academics_abcgeneralassignment`, `academics_abcinternalexam`, `academics_progressionconfig`, `academics_progressioncase`, `academics_promotionrun` | term→academic_year, examscore→student+term, reportcard→student+term, progressioncase→student+config | unique_together on many models |
| admissions | `admissions_admissiongrade`, `admissions_applicant`, `admissions_applicanttimelineentry`, `admissions_applicantdocumentreceipt`, `admissions_assessmentschedule`, `admissions_applicantinternalnote`, `admissions_enrolmentchecklist` | status+grade, inquiry_channel+created_at | |
| students | `students_student`, `students_studentsibling`, `students_parentguardian`, `students_pdpaconsentlog`, `students_studentguardian`, `students_laravelparent`, `students_studentlaravelparent`, `students_enrollmenthistory` | student→academic_year, enrollmenthistory→student | |
| attendance | `attendance_attendanceentry`, `attendance_staffattendanceentry`, `attendance_otpcode`, `attendance_message`, `attendance_notificationlog` | student+date, notification status | |
| communications | `communications_notification`, `communications_broadcast`, `communications_weeklyfocus`, `communications_phoneotp` | recipient+is_read, recipient+created_at | unique_together on weekly_focus |
| discipline | `discipline_disciplineincident` | student+status, severity+status | |
| events | `events_calendarevent`, `events_eventacknowledgement` | source_model+source_id | unique_together on event+parent |
| finance | `finance_feestructure`, `finance_feestructureitem`, `finance_financeperiod`, `finance_openingbalance`, `finance_invoice`, `finance_invoicelineitem`, `finance_financeconfig`, `finance_payment`, `finance_unmatchedpayment`, `finance_budget`, `finance_recurringexpense`, `finance_expense`, `finance_reminderconfiguration`, `finance_concession` | invoice→student, payment→invoice, invoice_number unique | unique_together on many; singleton on financeconfig |
| hr | `hr_staffprofile`, `hr_staffdocument`, `hr_teacherclassassignment`, `hr_onboardingchecklistitem`, `hr_staffonboardingprogress`, `hr_inductionchecklistcompletion`, `hr_payrollrun`, `hr_payrollentry`, `hr_statutoryfiling`, `hr_payrollconfig`, `hr_payetaxband`, `hr_leaveallocation`, `hr_leaverequest`, `hr_offboardingrecord` | unique_together on many; singleton on payrollconfig | |
| ptc | `ptc_ptcwindow`, `ptc_enrichmentsubject`, `ptc_enrichmentgrade`, `ptc_ptcsubjectcomment`, `ptc_learnerattributeratingentry`, `ptc_ptcgenerationlog`, `ptc_ptcprogresstrendoverride`, `ptc_ptcdatechangelog` | student+term, ptc_window+student | unique_together on many |
| tasks | `tasks_task`, `tasks_taskhistory`, `tasks_taskcomment`, `tasks_tasktemplate`, `tasks_tasknotificationlog` | assigned_to+status, assigned_to+due_date | |
| timetable | `timetable_timetableslot`, `timetable_ecdbreakconfig` | term+class+day+start, term+teacher+day+start | |
| welfare | `welfare_welfareobservation`, `welfare_welfareacknowledgment` | student+observation_date, severity+hod_status | unique_together on observation+user |
| audit | `audit_auditlog` | actor, action_type | |

---

## 9. KEY BUSINESS FLOWS

### Progression Workflow (Items 1–17)
```
calculate_progression_cases()
    ↓ (creates ProgressionCase at CALCULATED)
HOD Review List → HOD Action (PENDING_HOD_REVIEW)
    ↓
HOS Decision List → HOS Decision (PENDING_HOS_DECISION)
    ↓ (or HOS returns to HOD → PENDING_HOD_REVIEW)
Bulk Promotion Execute (FINALIZED)
    ↓
Student promoted/retained/graduated
EnrollmentHistory created
Notification sent to parents/HOD
```

### Report Card Workflow
```
Teacher enters scores (DRAFT)
    ↓
Teacher submits (SUBMITTED)
    ↓
HOD approves scores (APPROVED)
    ↓
Teacher enters comments
    ↓
HOS signs off report (PUBLISHED)
```

### Attendance Workflow
```
QR scan or manual check-in
    ↓
AttendanceEntry created
    ↓
Real-time broadcast via WebSocket
    ↓
Notification sent (SMS/email) if absent
    ↓
Parent can view via parent portal
```
