# Generated migration for dynamic exam type configuration

from django.db import migrations, models
import django.db.models.deletion


def create_default_configurations(apps, schema_editor):
    ExamTypeConfiguration = apps.get_model('academics', 'ExamTypeConfiguration')
    ECDTemplateConfiguration = apps.get_model('academics', 'ECDTemplateConfiguration')
    
    # Default exam types
    default_exam_types = [
        {
            'name': 'Quiz',
            'code': 'quiz',
            'description': 'Regular quiz assessment',
            'weight_percentage': 20.00,
            'max_score': 100.00,
            'display_order': 1,
            'is_active': True
        },
        {
            'name': 'Mid-term',
            'code': 'mid_term',
            'description': 'Mid-term examination',
            'weight_percentage': 30.00,
            'max_score': 100.00,
            'display_order': 2,
            'is_active': True
        },
        {
            'name': 'End of Term',
            'code': 'end_of_term',
            'description': 'End of term examination',
            'weight_percentage': 50.00,
            'max_score': 100.00,
            'display_order': 3,
            'is_active': True
        }
    ]
    
    for exam_type_data in default_exam_types:
        ExamTypeConfiguration.objects.get_or_create(
            code=exam_type_data['code'],
            defaults=exam_type_data
        )
    
    # Default ECD templates
    default_ecd_templates = [
        {
            'name': 'Pre-Kindergarten',
            'code': 'pre_k',
            'description': 'Pre-Kindergarten assessment template',
            'display_order': 1,
            'is_active': True
        },
        {
            'name': 'Kindergarten',
            'code': 'kindergarten',
            'description': 'Kindergarten assessment template',
            'display_order': 2,
            'is_active': True
        },
        {
            'name': 'Pre-School (RR)',
            'code': 'pre_school',
            'description': 'Pre-School assessment template',
            'display_order': 3,
            'is_active': True
        },
        {
            'name': 'ABC Class',
            'code': 'abc',
            'description': 'ABC Class assessment template',
            'display_order': 4,
            'is_active': True
        }
    ]
    
    for template_data in default_ecd_templates:
        ECDTemplateConfiguration.objects.get_or_create(
            code=template_data['code'],
            defaults=template_data
        )

def reverse_create_default_configurations(apps, schema_editor):
    ExamTypeConfiguration = apps.get_model('academics', 'ExamTypeConfiguration')
    ECDTemplateConfiguration = apps.get_model('academics', 'ECDTemplateConfiguration')
    ExamTypeConfiguration.objects.filter(code__in=['quiz', 'mid_term', 'end_of_term']).delete()
    ECDTemplateConfiguration.objects.filter(code__in=['pre_k', 'kindergarten', 'pre_school', 'abc']).delete()

class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0005_examscore_approved_at_examscore_approved_by_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExamTypeConfiguration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=50, unique=True)),
                ('code', models.CharField(help_text='Unique code for internal use', max_length=20, unique=True)),
                ('description', models.CharField(blank=True, max_length=100)),
                ('weight_percentage', models.DecimalField(decimal_places=2, help_text='Weight percentage for overall calculation (e.g., 20.00 for 20%)', max_digits=5)),
                ('max_score', models.DecimalField(decimal_places=2, default=100, help_text='Maximum score for this exam type', max_digits=5)),
                ('is_active', models.BooleanField(default=True)),
                ('display_order', models.PositiveIntegerField(default=0, help_text='Order in forms and reports')),
            ],
            options={
                'verbose_name': 'Exam Type Configuration',
                'verbose_name_plural': 'Exam Type Configurations',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='ECDTemplateConfiguration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=50, unique=True)),
                ('code', models.CharField(max_length=20, unique=True)),
                ('description', models.CharField(blank=True, max_length=100)),
                ('is_active', models.BooleanField(default=True)),
                ('display_order', models.PositiveIntegerField(default=0, help_text='Order in forms and reports')),
            ],
            options={
                'verbose_name': 'ECD Template Configuration',
                'verbose_name_plural': 'ECD Template Configurations',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='examscore',
            name='exam_type_config',
            field=models.ForeignKey(blank=True, help_text='Reference to exam type configuration', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='exam_scores', to='academics.examtypeconfiguration'),
        ),
        migrations.RunPython(
            create_default_configurations,
            reverse_code=reverse_create_default_configurations,
        ),
    ]
