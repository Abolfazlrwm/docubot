"""Install the pgvector extension before any vector column is created."""

from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0001_initial"),
    ]

    operations = [
        VectorExtension(),
    ]
