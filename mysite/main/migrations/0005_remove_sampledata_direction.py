# Generated by Django 5.0.7 on 2024-08-13 04:38

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0004_agency_details_property_delete_sampleagentdata_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='sampledata',
            name='direction',
        ),
    ]
