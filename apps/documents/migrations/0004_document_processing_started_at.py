import uuid

from django.conf import settings
from django.db import models, migrations
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0003_chunk"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="processing_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
