"""
Copyright 2019 Red Hat, Inc.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import json
import logging
import uuid
from typing import Callable, FrozenSet, Type

from management.role.model import Role
from management.workspace.model import Workspace
from migration_tool.ingest import add_element
from migration_tool.models import (
    V1group,
    V1permission,
    V1resourcedef,
    V1role,
    V2boundresource,
    V2group,
    V2role,
    V2rolebinding,
)
from migration_tool.models import cleanNameForV2SchemaCompatibility


logger = logging.getLogger(__name__)

Permissiongroupings = dict[V1resourcedef, list[str]]
Perm_bound_resources = dict[str, list[V2boundresource]]

group_perms_for_rolebinding_fn = Type[
    Callable[
        [str, Permissiongroupings, Perm_bound_resources, FrozenSet[V1group]],
        FrozenSet[V2rolebinding],
    ]
]


def add_system_role(system_roles, role: V2role):
    """Add a system role to the system role map."""
    system_roles[frozenset(role.permissions)] = role


def inventory_to_workspace(v2_perm):
    """Convert inventory permissions to workspace permissions."""
    if v2_perm == "inventory_groups_read":
        return "workspace_read"
    elif v2_perm == "inventory_groups_write":
        return "workspace_write"
    elif v2_perm == "inventory_groups_all":
        return "workspace_all"
    return v2_perm


class SystemRole:
    """A system role."""

    SYSTEM_ROLES = {}

    @classmethod
    def get_system_roles(cls):
        """Get the system roles, if empty, set them."""
        if not cls.SYSTEM_ROLES:
            cls.set_system_roles()
        return cls.SYSTEM_ROLES

    @classmethod
    def set_system_roles(cls):
        """Set the system roles."""
        for role in Role.objects.filter(system=True):
            # Skip roles such as OCM since they don't have permission
            if role.external_role_id():
                continue
            permission_list = list()
            for access in role.access.all():
                v2_perm = cleanNameForV2SchemaCompatibility(access.permission.permission)
                v2_perm = inventory_to_workspace(v2_perm)
                permission_list.append(v2_perm)
            add_system_role(cls.SYSTEM_ROLES, V2role(str(role.uuid), True, frozenset(permission_list)))


skipped_apps = {"cost-management", "playbook-dispatcher", "approval"}


def v1_role_to_v2_mapping(v1_role: V1role, root_workspace: str, default_workspace: str) -> FrozenSet[V2rolebinding]:
    """Convert a V1 role to a set of V2 role bindings."""
    perm_groupings: Permissiongroupings = {}
    # Group V2 permissions by target
    for v1_perm in v1_role.permissions:
        if not is_for_enabled_app(v1_perm):
            continue
        v2_perm = v1_perm_to_v2_perm(v1_perm)
        if v1_perm.resourceDefs:
            for resource_def in v1_perm.resourceDefs:
                resource_type = (
                    "workspace"
                    if v1_perm.app == "inventory"
                    else v1_attributefilter_resource_type_to_v2_resource_type(resource_def.resource_type)
                )
                for resource_id in split_resourcedef_literal(resource_def):
                    if resource_type == "workspace":
                        if resource_id is None:
                            resource_id = default_workspace
                    add_element(
                        perm_groupings,
                        V2boundresource(resource_type, resource_id),
                        v2_perm,
                    )
        else:
            add_element(
                perm_groupings,
                V2boundresource("workspace", root_workspace),
                v2_perm,
            )
    # Project permission sets to system roles
    resource_roles = extract_system_roles(perm_groupings, v1_role)

    # Construct rolebindings
    v2_role_bindings = []
    v2_groups = v1groups_to_v2groups(v1_role.groups)
    for role, resources in resource_roles.items():
        for resource in resources:
            if v2_groups:
                for v2_group in v2_groups:
                    role_binding_id = str(uuid.uuid4())
                    v2_role_binding = V2rolebinding(
                        role_binding_id, v1_role, role, frozenset({resource}), frozenset({v2_group})
                    )
                    v2_role_bindings.append(v2_role_binding)
            else:
                role_binding_id = str(uuid.uuid4())
                v2_role_binding = V2rolebinding(role_binding_id, v1_role, role, frozenset({resource}), v2_groups)
                v2_role_bindings.append(v2_role_binding)
    return frozenset(v2_role_bindings)


candidate_system_roles = {}
custom_roles_created = 0


def extract_system_roles(perm_groupings, v1_role):
    """Extract system roles from a set of permissions."""
    resource_roles = {}
    system_roles = SystemRole.get_system_roles()
    for resource, permissions in perm_groupings.items():
        system_role = system_roles.get(frozenset(permissions))
        if system_role is not None:
            role = system_roles[frozenset(permissions)]
            add_element(resource_roles, role, resource)
        else:
            permset = set(permissions)
            granted = set()
            matched_roles = []

            for sysperms, sysrole in system_roles.items():
                if sysperms.issubset(permset) and not sysperms.issubset(
                    granted
                ):  # If all permissions on the role should be granted but not all of them have been, add it
                    matched_roles.append(sysrole)
                    granted |= sysperms

                if permset == granted:
                    break
            if permset == granted:
                for role in matched_roles:
                    add_element(resource_roles, role, resource)
            else:
                # Track leftovers and add a custom role
                leftovers = permset - granted
                logger.info(
                    f"No system role for role: {v1_role.id}. Not matched permissions: {leftovers}. Resource: {resource}"
                )
                # Track possible missing system roles
                # Get applications with unmatched permissions
                apps = {}
                for perm in leftovers:
                    app = perm.split("_", 1)[0]  # Hack since we don't have the V1 data anymore by this point
                    if app not in apps:
                        apps[app] = []
                # Get original permissions granted on this resource grouped by application,
                # for applications with unmatched permissions
                for perm in permissions:
                    app = perm.split("_", 1)[0]  # Hack since we don't have the V1 data anymore by this point
                    if app in apps:
                        apps[app].append(perm)
                # Increment counts for each distinct set of permissions

                for app, perms in apps.items():
                    candidate = frozenset(perms)
                    if candidate in candidate_system_roles:
                        candidate_system_roles[candidate].add(v1_role.id)
                    else:
                        candidate_system_roles[candidate] = {v1_role.id}
                # Add a custom role
                add_element(resource_roles, V2role(str(uuid.uuid4()), False, frozenset(permissions)), resource)
                global custom_roles_created
                custom_roles_created += 1
    return resource_roles


def is_for_enabled_app(perm: V1permission):
    """Return true if the permission is for an app that is no longer in use."""
    return perm.app not in skipped_apps


def split_resourcedef_literal(resourceDef: V1resourcedef):
    """Split a resource definition into a list of resource IDs."""
    if resourceDef.op == "in":
        try:
            return json.loads(resourceDef.resource_id)  # Most are JSON arrays
        except json.JSONDecodeError:
            return resourceDef.resource_id.split(
                ","
            )  # If not JSON, assume comma-separated? Cost Management openshift assets are like this.
    else:
        return [json.loads(resourceDef.resource_id)]


def shared_system_role_replicated_role_bindings_v1_to_v2_mapping(
    v1_role: V1role, root_workspace: Workspace, default_workspace: Workspace
) -> FrozenSet[V2rolebinding]:
    """Convert a V1 role to a set of V2 role bindings."""
    return v1_role_to_v2_mapping(v1_role, root_workspace, default_workspace)


def v1groups_to_v2groups(v1groups: FrozenSet[V1group]):
    """Convert a set of V1 groups to a set of V2 groups."""
    return frozenset([V2group(v1group.id, v1group.users) for v1group in v1groups])


def v1_perm_to_v2_perm(v1_permission):
    """Convert a V1 permission to a V2 permission."""
    if v1_permission.app == "inventory" and v1_permission.resource == "groups":
        return cleanNameForV2SchemaCompatibility(f"workspace_{v1_permission.perm}")
    return cleanNameForV2SchemaCompatibility(
        v1_permission.app + "_" + v1_permission.resource + "_" + v1_permission.perm
    )


def v1_attributefilter_resource_type_to_v2_resource_type(resourceType: str):  # Format is app.type
    """Convert a V1 resource type to a V2 resource type."""
    parts = resourceType.split(".", 1)
    app = cleanNameForV2SchemaCompatibility(parts[0])
    resource = cleanNameForV2SchemaCompatibility(parts[1])
    return f"{app}/{resource}"
