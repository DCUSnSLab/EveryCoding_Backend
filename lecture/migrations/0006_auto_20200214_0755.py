# Generated by Django 2.1.7 on 2020-02-14 07:55

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('lecture', '0005_lecture_password'),
    ]

    operations = [
        migrations.AlterField(
            model_name='lecture',
            name='password',
            field=models.TextField(),
        ),
    ]