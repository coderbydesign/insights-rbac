#
# Copyright 2019 Red Hat, Inc.
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
"""API models for import organization."""
from tenant_schemas.models import TenantMixin

from api.cross_access.model import CrossAccountRequest  # noqa: F401
from api.status.model import Status  # noqa: F401

from django.core.management import call_command
from django.db import connection, transaction
from django.db.models.signals import post_save

from tenant_schemas.postgresql_backend.base import _check_schema_name
from tenant_schemas.utils import get_public_schema_name, schema_exists


class Tenant(TenantMixin):
    """The model used to create a tenant schema."""

    # Override the mixin domain url to make it nullable, non-unique
    domain_url = None

    # Delete all schemas when a tenant is removed
    auto_drop_schema = True

    def __str__(self):
        """Get string representation of Tenant."""
        return f"Tenant ({self.schema_name})"

    def save(self, *args, **kwargs):
        is_new = self.pk is None

        if is_new and connection.schema_name != get_public_schema_name():
            raise Exception(
                "Can't create tenant outside the public schema. " "Current schema is %s." % connection.schema_name
            )
        elif not is_new and connection.schema_name not in (self.schema_name, get_public_schema_name()):
            raise Exception(
                "Can't update tenant outside it's own schema or "
                "the public schema. Current schema is %s." % connection.schema_name
            )

        super(TenantMixin, self).save(*args, **kwargs)

    def create_schema(self, check_if_exists=False, sync_schema=True, verbosity=1):
        from management.group.definer import seed_group  # noqa: I100, I201
        from management.role.definer import seed_permissions, seed_roles

        """
        Creates the schema 'schema_name' for this tenant. Optionally checks if
        the schema already exists before creating it. Returns true if the
        schema was created, false otherwise.
        """

        # safety check
        _check_schema_name(self.schema_name)
        cursor = connection.cursor()

        if check_if_exists and schema_exists(self.schema_name):
            return False

        # create the schema
        cursor.execute("CREATE SCHEMA %s" % self.schema_name)

        if sync_schema:
            call_command("migrate_schemas", schema_name=self.schema_name, interactive=False, verbosity=verbosity)

        seed_permissions(tenant=self)
        seed_roles(tenant=self)
        seed_group(tenant=self)

        connection.set_schema_to_public()


class User:
    """A request User."""

    username = None
    account = None
    admin = False
    access = {}
    system = False
    is_active = True


def schema_handler(sender=None, instance=None, using=None, **kwargs):
    """Signal handler for creating a schema."""
    transaction.on_commit(lambda: instance.create_schema(check_if_exists=True, verbosity=1))


post_save.connect(schema_handler, sender=Tenant)
