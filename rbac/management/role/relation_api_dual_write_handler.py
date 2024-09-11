#
# Copyright 2024 Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

"""Class to handle Dual Write API related operations."""
import logging

from management.models import Outbox
from management.role.model import BindingMapping
from migration_tool.migrate import migrate_role
from migration_tool.utils import relationship_to_json

from rbac.env import ENVIRONMENT

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class DualWriteException(Exception):
    """DualWrite exception."""

    pass


class RelationApiDualWriteHandler:
    """Class to handle Dual Write API related operations."""

    def __init__(self, role, event_type):
        """Initialize RelationApiDualWriteHandler."""
        if not self.replication_enabled():
            return
        try:
            self.role_relations = []
            self.current_role_relations = []
            self.role = role
            self.binding_mapping = None
            self.tenant_id = role.tenant_id
            self.org_id = role.tenant.org_id
            self.event_type = event_type
        except Exception as e:
            raise DualWriteException(e)

    def replication_enabled(self):
        """Check whether replication enabled."""
        return ENVIRONMENT.get_value("REPLICATION_TO_RELATION_ENABLED", default=False, cast=bool)

    def get_current_role_relations(self):
        """Get current roles relations."""
        return self.current_role_relations

    def load_relations_from_current_state_of_role(self):
        """Generate relations from current state of role and UUIDs for v2 role and role binding from database."""
        if not self.replication_enabled():
            return
        try:
            logger.info(
                "[Dual Write] Generate relations from current state of role(%s): '%s'", self.role.uuid, self.role.name
            )

            self.binding_mapping = self.role.binding_mapping

            relations, _ = migrate_role(
                self.role,
                write_relationships=False,
                default_workspace=self.org_id,
                current_mapping=self.binding_mapping,
            )

            self.current_role_relations = relations
        except BindingMapping.DoesNotExist:
            logger.warning(
                "[Dual Write] Binding mapping not found for role(%s): '%s'. "
                "Assuming no current relations exist. "
                "If this is NOT the case, relations are inconsistent!",
                self.role.uuid,
                self.role.name,
            )
        except Exception as e:
            raise DualWriteException(e)

    def generate_replication_event_to_outbox(self, role):
        """Generate replication event to outbox table."""
        if not self.replication_enabled():
            return
        self.role = role
        self._generate_relations_and_mappings_for_role()
        return self.save_replication_event_to_outbox()

    def save_replication_event_to_outbox(self):
        """Generate and store replication event to outbox table."""
        if not self.replication_enabled():
            return {}
        try:
            replication_event = self._build_replication_event()
            self._save_replication_event(replication_event)
        except Exception as e:
            raise DualWriteException(e)
        return replication_event

    def _generate_relations_and_mappings_for_role(self):
        """Generate relations and mappings for a role with new UUIDs for v2 role and role bindings."""
        if not self.replication_enabled():
            return []
        try:
            logger.info("[Dual Write] Generate new relations from role(%s): '%s'", self.role.uuid, self.role.name)

            relations, mappings = migrate_role(
                self.role,
                write_relationships=False,
                default_workspace=self.org_id,
                current_mapping=self.binding_mapping,
            )

            self.role_relations = relations

            if self.binding_mapping is None:
                self.binding_mapping = BindingMapping.objects.create(role=self.role, mappings=mappings)
            else:
                self.binding_mapping.mappings = mappings
                self.binding_mapping.save(force_update=True)

            return relations
        except Exception as e:
            raise DualWriteException(e)

    def _build_replication_event(self):
        """Build replication event."""
        if not self.replication_enabled():
            return {}
        logger.info("[Dual Write] Build Replication event for role(%s): '%s'", self.role.uuid, self.role.name)
        relations_to_add = []
        for relation in self.role_relations:
            relations_to_add.append(relationship_to_json(relation))

        relations_to_remove = []
        for relation in self.current_role_relations:
            relations_to_remove.append(relationship_to_json(relation))

        replication_event = {"relations_to_add": relations_to_add, "relations_to_remove": relations_to_remove}
        return replication_event

    def _save_replication_event(self, replication_event):
        """Save replication event."""
        if not self.replication_enabled():
            return
        logger.info(
            "[Dual Write] Save replication event into outbox table for role(%s): '%s'", self.role.uuid, self.role.name
        )
        logger.info(
            "[Dual Write] Replication event: %s for role(%s): '%s'", replication_event, self.role.uuid, self.role.name
        )
        # https://debezium.io/documentation/reference/stable/transformations/outbox-event-router.html#basic-outbox-table
        outbox_record = Outbox.objects.create(
            aggregatetype="Role", aggregateid=self.role.uuid, event_type=self.event_type, payload=replication_event
        )
        outbox_record.delete()