# -*- coding: utf-8 -*-
# Generated by Django 1.11.9 on 2018-01-30 14:13
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_auto_20180119_1055'),
    ]

    operations = [
        migrations.AlterField(
            model_name='message',
            name='task_id',
            field=models.CharField(max_length=128),
        ),
    ]
