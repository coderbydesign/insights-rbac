# Generated by Django 4.2.7 on 2024-01-15 17:10

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0014_auto_20220726_1743"),
        ("management", "0042_service_accounts_update_usernames_format_RHCLOUD_29429"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created", models.DateTimeField(default=django.utils.timezone.now)),
                ("principal_username", models.TextField(max_length=255)),
                ("description", models.TextField(max_length=255)),
                (
                    "resource_type",
                    models.CharField(
                        choices=[
                            ("group", "Group"),
                            ("role", "Role"),
                            ("user", "User"),
                            ("permission", "Permission"),
                        ],
                        max_length=32,
                    ),
                ),
                ("resource_id", models.IntegerField(null=True)),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("delete", "Delete"),
                            ("add", "Add"),
                            ("edit", "Edit"),
                            ("create", "Create"),
                            ("remove", "Remove"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "principal",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="management.principal",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="api.tenant"),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
    ]
