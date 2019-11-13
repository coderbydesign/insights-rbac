#
# Copyright 2019 Red Hat, Inc.
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

"""Serializer for group management."""
from management.group.model import Group
from management.principal.proxy import PrincipalProxy
from management.principal.serializer import PrincpalInputSerializer, PrincpalSerializer
from management.role.model import Role
from management.role.serializer import RoleMinimumSerializer
from rest_framework import serializers, status
from rest_framework.validators import UniqueValidator


class GroupInputSerializer(serializers.ModelSerializer):
    """Serializer for Group input model."""

    uuid = serializers.UUIDField(read_only=True)
    name = serializers.CharField(required=True,
                                 max_length=150,
                                 validators=[UniqueValidator(queryset=Group.objects.all())])
    description = serializers.CharField(allow_null=True, required=False)
    principalCount = serializers.IntegerField(read_only=True)
    created = serializers.DateTimeField(read_only=True)
    modified = serializers.DateTimeField(read_only=True)
    roleCount = serializers.SerializerMethodField()

    def get_roleCount(self, obj):
        """Role count for the serializer."""
        policy_ids = obj.policies.values_list('id', flat=True)
        return Role.objects.filter(policies__in=policy_ids).distinct().count()

    class Meta:
        """Metadata for the serializer."""

        model = Group
        fields = ('uuid', 'name', 'description',
                  'principalCount', 'roleCount',
                  'created', 'modified')


class GroupSerializer(serializers.ModelSerializer):
    """Serializer for the Group model."""

    uuid = serializers.UUIDField(read_only=True)
    name = serializers.CharField(required=True, max_length=150)
    description = serializers.CharField(allow_null=True, required=False)
    principals = PrincpalSerializer(read_only=True, many=True)
    created = serializers.DateTimeField(read_only=True)
    modified = serializers.DateTimeField(read_only=True)
    roles = serializers.SerializerMethodField()

    def get_roles(self, obj):
        """Role constructor for the serializer."""
        policy_ids = obj.policies.values_list('id', flat=True)
        roles = Role.objects.filter(policies__in=policy_ids).distinct()
        serialized_roles = [RoleMinimumSerializer(role).data for role in roles]
        return serialized_roles

    class Meta:
        """Metadata for the serializer."""

        model = Group
        fields = ('uuid', 'name', 'description', 'principals', 'created',
                  'modified', 'roles')

    def to_representation(self, obj):
        """Convert representation to dictionary object."""
        proxy = PrincipalProxy()
        formatted = super().to_representation(obj)
        principals = formatted.pop('principals')
        users = [principal.get('username') for principal in principals]
        resp = proxy.request_filtered_principals(users, limit=len(users))
        if resp.get('status_code') == status.HTTP_200_OK:
            principals = resp.get('data')
        formatted['principals'] = principals
        return formatted


class GroupPrincipalInputSerializer(serializers.Serializer):
    """Serializer for adding principals to a group."""

    principals = PrincpalInputSerializer(many=True)

    class Meta:
        """Metadata for the serializer."""

        fields = ('principals',)
