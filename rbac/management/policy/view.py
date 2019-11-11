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

"""View for policy management."""
from django_filters import rest_framework as filters
from management.permissions import PolicyAccessPermission
from management.querysets import get_policy_queryset
from rest_framework import mixins, viewsets
from rest_framework.filters import OrderingFilter

from .model import Policy
from .serializer import PolicyInputSerializer, PolicySerializer


class PolicyFilter(filters.FilterSet):
    """Filter for policy."""

    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    group_name = filters.CharFilter(field_name='group', lookup_expr='name__icontains')
    group_uuid = filters.UUIDFilter(field_name='group', lookup_expr='uuid__exact')

    class Meta:
        model = Policy
        fields = ['name', 'group_name', 'group_uuid']


class PolicyViewSet(mixins.CreateModelMixin,
                    mixins.DestroyModelMixin,
                    mixins.ListModelMixin,
                    mixins.RetrieveModelMixin,
                    mixins.UpdateModelMixin,
                    viewsets.GenericViewSet):
    """Policy View.

    A viewset that provides default `create()`, `destroy`, `retrieve()`,
    and `list()` actions.

    """

    queryset = Policy.objects.all()
    permission_classes = (PolicyAccessPermission,)
    lookup_field = 'uuid'
    filter_backends = (filters.DjangoFilterBackend, OrderingFilter)
    filterset_class = PolicyFilter
    ordering_fields = ('name', 'modified')
    ordering = ('name',)

    def get_queryset(self):
        """Obtain queryset for requesting user based on access."""
        return get_policy_queryset(self.request)

    def get_serializer_class(self):
        """Get serializer based on route."""
        if self.request.method in ('POST', 'PUT'):
            return PolicyInputSerializer
        return PolicySerializer

    def create(self, request, *args, **kwargs):
        """Create a policy."""
        return super().create(request=request, args=args, kwargs=kwargs)

    def list(self, request, *args, **kwargs):
        """Obtain the list of policies for the tenant."""
        return super().list(request=request, args=args, kwargs=kwargs)

    def retrieve(self, request, *args, **kwargs):
        """Get a policy."""
        return super().retrieve(request=request, args=args, kwargs=kwargs)

    def destroy(self, request, *args, **kwargs):
        """Delete a policy."""
        return super().destroy(request=request, args=args, kwargs=kwargs)

    def update(self, request, *args, **kwargs):
        """Update a policy."""
        return super().update(request=request, args=args, kwargs=kwargs)
