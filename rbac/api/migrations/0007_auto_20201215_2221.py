# Generated by Django 2.2.4 on 2020-12-15 22:21

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [("management", "0030_auto_20201130_1845"), ("api", "0006_auto_20201208_0045")]

    operations = [
        migrations.CreateModel(
            name="RequestsRoles",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "cross_account_request",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="api.CrossAccountRequest"),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cross_account_requests",
                        to="management.Role",
                        to_field="uuid",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="crossaccountrequest",
            name="roles",
            field=models.ManyToManyField(through="api.RequestsRoles", to="management.Role"),
        ),
    ]
