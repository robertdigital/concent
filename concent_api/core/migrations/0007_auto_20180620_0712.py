# -*- coding: utf-8 -*-
# Generated by Django 1.11.9 on 2018-06-20 07:12
from __future__ import unicode_literals

from django.db import migrations
import django.db.models.manager


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_add_and_update_deadline_and_size'),
    ]

    operations = [
        migrations.AlterModelManagers(
            name='subtask',
            managers=[
                ('objects_with_timing_columns', django.db.models.manager.Manager()),
            ],
        ),
    ]
