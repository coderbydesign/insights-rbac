"""Command to provision test tenants and principals for local/integration testing."""

import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from management.principal.model import Principal
from management.relation_replicator.logging_replicator import LoggingReplicator
from management.relation_replicator.outbox_replicator import OutboxReplicator
from management.tenant_service.v2 import V2TenantBootstrapService

from api.models import Tenant
from rbac.settings import REPLICATION_TO_RELATION_ENABLED

logger = logging.getLogger(__name__)

# Test personas: (username, user_id_offset, is_org_admin)
# user_id is computed as (org_index * 100) + offset to ensure global uniqueness.
DEFAULT_PERSONAS = [
    ("org_admin", 1, True),
    ("regular_user", 2, False),
    ("readonly_user", 3, False),
]

# Test orgs: (org_id, account_number)
DEFAULT_ORGS = [
    ("test_org_01", "acct_01"),
    ("test_org_02", "acct_02"),
    ("test_org_03", "acct_03"),
]


class Command(BaseCommand):
    """Provision test tenants and principals for integration testing."""

    help = "Create test tenants with multiple user personas for local/integration testing."

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--num-orgs",
            type=int,
            default=len(DEFAULT_ORGS),
            help=f"Number of test orgs to create (default: {len(DEFAULT_ORGS)})",
        )
        parser.add_argument(
            "--org-prefix",
            type=str,
            default="test_org",
            help="Prefix for org IDs (default: test_org)",
        )
        parser.add_argument(
            "--clean",
            action="store_true",
            help="Remove existing test tenants before provisioning",
        )

    def handle(self, *args, **options):
        """Run the provisioning."""
        num_orgs = options["num_orgs"]
        org_prefix = options["org_prefix"]
        clean = options["clean"]

        if REPLICATION_TO_RELATION_ENABLED:
            replicator = OutboxReplicator()
        else:
            replicator = LoggingReplicator()

        bootstrap_service = V2TenantBootstrapService(replicator=replicator)

        if clean:
            self._clean_test_data(org_prefix)

        orgs = DEFAULT_ORGS[:num_orgs] if num_orgs <= len(DEFAULT_ORGS) else self._generate_orgs(num_orgs, org_prefix)

        results = []

        for org_index, (org_id, account_number) in enumerate(orgs):
            self.stdout.write(f"Provisioning tenant: org_id={org_id}, account={account_number}")

            try:
                with transaction.atomic():
                    tenant, created = Tenant.objects.get_or_create(
                        org_id=org_id,
                        defaults={"tenant_name": f"acct{account_number}", "account_id": account_number, "ready": True},
                    )

                    if not created:
                        self.stdout.write(f"  Tenant {org_id} already exists, skipping bootstrap")
                    else:
                        bootstrap_service.bootstrap_tenant(tenant)
                        self.stdout.write(f"  Tenant {org_id} bootstrapped")

                    # Create principals for each persona
                    for username, uid_offset, is_org_admin in DEFAULT_PERSONAS:
                        user_id = str(100000 + org_index * 100 + uid_offset)
                        full_username = f"{username}_{org_id}"
                        principal, p_created = Principal.objects.get_or_create(
                            username=full_username,
                            tenant=tenant,
                            defaults={"user_id": user_id, "type": Principal.Types.USER},
                        )
                        action = "created" if p_created else "exists"
                        self.stdout.write(f"  Principal: {full_username} (user_id={user_id}) [{action}]")

                    results.append((org_id, account_number, True, None, org_index))

            except Exception as e:
                logger.error(f"Failed to provision tenant {org_id}: {e}", exc_info=True)
                results.append((org_id, account_number, False, str(e), org_index))

        self._print_summary(results)

    def _generate_orgs(self, num_orgs, prefix):
        """Generate org tuples beyond the defaults."""
        orgs = []
        for i in range(1, num_orgs + 1):
            orgs.append((f"{prefix}_{i:02d}", f"acct_{i:02d}"))
        return orgs

    def _clean_test_data(self, org_prefix):
        """Remove test tenants matching the prefix and their related objects."""
        from management.workspace.model import Workspace

        test_tenants = Tenant.objects.filter(org_id__startswith=org_prefix)
        count = test_tenants.count()
        if count > 0:
            # Delete workspaces leaf-first (self-referencing protected FK on parent)
            ws_qs = Workspace.objects.filter(tenant__in=test_tenants)
            while ws_qs.exists():
                leaves = ws_qs.exclude(id__in=Workspace.objects.filter(parent__in=ws_qs).values("parent_id"))
                if not leaves.exists():
                    break
                leaves.delete()
            Principal.objects.filter(tenant__in=test_tenants).delete()
            test_tenants.delete()
            self.stdout.write(f"Cleaned {count} existing test tenant(s)")

    def _print_summary(self, results):
        """Print a reference table of provisioned test data."""
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("TEST DATA SUMMARY")
        self.stdout.write("=" * 80)

        succeeded = [r for r in results if r[2]]
        failed = [r for r in results if not r[2]]

        if succeeded:
            self.stdout.write(f"\nProvisioned {len(succeeded)} tenant(s):\n")
            self.stdout.write(f"{'Org ID':<20} {'Account':<15} {'Username':<35} {'User ID':<10} {'Org Admin'}")
            self.stdout.write("-" * 95)

            for org_id, account, _, _, org_index in succeeded:
                for i, (username, uid_offset, is_admin) in enumerate(DEFAULT_PERSONAS):
                    user_id = str(100000 + org_index * 100 + uid_offset)
                    full_username = f"{username}_{org_id}"
                    org_col = org_id if i == 0 else ""
                    acct_col = account if i == 0 else ""
                    self.stdout.write(f"{org_col:<20} {acct_col:<15} {full_username:<35} {user_id:<10} {is_admin}")
                self.stdout.write("")

        if failed:
            self.stdout.write(f"\nFailed to provision {len(failed)} tenant(s):")
            for org_id, _, _, error, _ in failed:
                self.stdout.write(f"  {org_id}: {error}")

        self.stdout.write("\nDev middleware usage examples:")
        if succeeded:
            org_id = succeeded[0][0]
            self.stdout.write(
                f'  curl -H "X-Dev-Org-Id: {org_id}" '
                f'-H "X-Dev-Username: org_admin_{org_id}" '
                f"http://localhost:8000/api/rbac/v1/roles/"
            )
            self.stdout.write(
                f'  curl -H "X-Dev-Org-Id: {org_id}" '
                f'-H "X-Dev-Username: regular_user_{org_id}" '
                f'-H "X-Dev-Is-Org-Admin: false" '
                f"http://localhost:8000/api/rbac/v1/roles/"
            )
        self.stdout.write("=" * 80)
