# Generated by Django 2.1.7 on 2020-02-21 15:54

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('lecture', '0012_auto_20200221_0438'),
    ]

    operations = [
        migrations.AddField(
            model_name='lecture',
            name='isapply',
            field=models.BooleanField(default=False),
        ),
    ]