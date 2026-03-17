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

"""Custom RBAC Dev Middleware."""

import hashlib

from base64 import b64encode
from json import dumps as json_dumps

from django.utils.deprecation import MiddlewareMixin

from api.common import RH_IDENTITY_HEADER


class DevelopmentIdentityHeaderMiddleware(MiddlewareMixin):  # pylint: disable=too-few-public-methods
    """Middleware to add 3scale header for development."""

    header = RH_IDENTITY_HEADER

    def process_request(self, request):  # pylint: disable=no-self-use
        """Process request for to add header.

        Args:
            request (object): The request object

        Supports optional override headers for multi-tenant testing:
            X-Dev-Org-Id: override org_id (default: 11111)
            X-Dev-Account: override account_number (default: 10001)
            X-Dev-Username: override username (default: user_dev)
            X-Dev-Email: override email (default: user_dev@foo.com)
            X-Dev-User-Id: override user_id (default: derived from username)
            X-Dev-Is-Org-Admin: override is_org_admin, "true"/"false" (default: true)
            X-Dev-Is-Internal: override is_internal, "true"/"false" (default: true)

        """
        if hasattr(request, "META"):
            user_type = request.headers.get("User-Type")
            if user_type and user_type in ["associate", "internal", "turnpike"]:
                identity_header = {
                    "identity": {
                        "associate": {
                            "Role": ["role"],
                            "email": "associate_dev@bar.com",
                            "givenName": "Associate",
                            "surname": "dev",
                        },
                        "auth_type": "saml-auth",
                        "type": "Associate",
                    }
                }
            else:
                org_id = request.headers.get("X-Dev-Org-Id", "11111")
                account = request.headers.get("X-Dev-Account", "10001")
                username = request.headers.get("X-Dev-Username", "user_dev")
                email = request.headers.get("X-Dev-Email", f"{username}@foo.com")
                default_user_id = str(int(hashlib.sha256(username.encode()).hexdigest()[:8], 16))
                user_id = request.headers.get("X-Dev-User-Id", default_user_id)
                is_org_admin = request.headers.get("X-Dev-Is-Org-Admin", "true").lower() == "true"
                is_internal = request.headers.get("X-Dev-Is-Internal", "true").lower() == "true"

                identity_header = {
                    "identity": {
                        "account_number": account,
                        "org_id": org_id,
                        "type": "User",
                        "user": {
                            "username": username,
                            "email": email,
                            "is_org_admin": is_org_admin,
                            "is_internal": is_internal,
                            "user_id": user_id,
                        },
                        "internal": {"cross_access": False},
                    }
                }
            json_identity = json_dumps(identity_header)
            dev_header = b64encode(json_identity.encode("utf-8"))
            request.META[self.header] = dev_header
