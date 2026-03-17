# RBAC Integration Testing Deployment

Deploy RBAC into any Kubernetes or OpenShift cluster for integration testing. This setup runs **only the API server** — no workers, schedulers, or background jobs are needed.

## What's Included

| Component | Description |
|-----------|-------------|
| PostgreSQL 16 | Database (StatefulSet with persistent storage) |
| Redis 7 | Cache (used by Django caching layer) |
| RBAC API Server | The main RBAC service, running in dev mode |
| Init Job | Runs migrations, seeds roles/permissions/groups, provisions test data |

**Not included** (not needed for API integration testing): Celery worker, Celery beat scheduler, Kafka, BOP/principal proxy, notifications.

## Prerequisites

- `kubectl` configured and pointing at your cluster
- `docker` (or `podman`) to build the container image
- Ability to make the built image available in your cluster (registry push, or local load)

## Step 1: Build the Image

From the repo root:

```bash
docker build -t insights-rbac:local .
```

Then make the image available to your cluster. How you do this depends on your environment:

| Environment | Command |
|-------------|---------|
| Push to a registry | `docker tag insights-rbac:local your-registry/insights-rbac:local && docker push your-registry/insights-rbac:local` |
| Kind | `kind load docker-image insights-rbac:local --name <cluster-name>` |
| Minikube | `minikube image load insights-rbac:local` |
| Colima (k3s) | `docker save insights-rbac:local \| colima ssh -- sudo ctr -n k8s.io images import -` |
| OpenShift with internal registry | Push to the internal registry, then update image refs in manifests |

> If you push to a registry, update the `image:` field in `rbac-server.yaml` and `init-job.yaml` to match (e.g. `your-registry.example.com/insights-rbac:local`).

## Step 2: Deploy

Apply the manifests in order:

```bash
kubectl apply -f deploy/local/namespace.yaml
kubectl apply -f deploy/local/configmap.yaml
kubectl apply -f deploy/local/postgres.yaml
kubectl apply -f deploy/local/redis.yaml
kubectl apply -f deploy/local/rbac-server.yaml
```

## Step 3: Wait for Dependencies and Run Init

```bash
# Wait for database and cache to be ready
kubectl -n rbac-local wait --for=condition=ready pod -l app=rbac-db --timeout=120s
kubectl -n rbac-local wait --for=condition=ready pod -l app=rbac-redis --timeout=60s

# Run migrations, seed permissions/roles/groups, provision test data
kubectl apply -f deploy/local/init-job.yaml
kubectl -n rbac-local wait --for=condition=complete job/rbac-init --timeout=300s

# Verify init succeeded
kubectl -n rbac-local logs job/rbac-init | tail -20
```

You should see a `TEST DATA SUMMARY` table showing 3 provisioned tenants.

## Step 4: Verify and Access the API

```bash
# Check all pods are running
kubectl -n rbac-local get pods

# Port-forward (or use your cluster's ingress/route)
kubectl -n rbac-local port-forward svc/rbac-server 8080:8080
```

Test it:

```bash
curl http://localhost:8080/api/rbac/v1/status/
# Expected: {"api_version":1,"commit":"undefined"}

curl http://localhost:8080/api/rbac/v1/roles/
# Expected: JSON with "meta": {"count": 35, ...}
```

---

## Dev Mode & Authentication

Dev mode is enabled (`DEVELOPMENT=True`), so **no authentication is required**. A dev middleware automatically injects an identity header on every request.

### Default Identity

Without any special headers, every request runs as:

| Field | Value |
|-------|-------|
| org_id | `11111` |
| username | `user_dev` |
| is_org_admin | `true` |
| is_internal | `true` |

### Switching Tenants and Users

Pass these request headers to impersonate different orgs and users:

| Header | Default | Description |
|--------|---------|-------------|
| `X-Dev-Org-Id` | `11111` | Override org/tenant ID |
| `X-Dev-Account` | `10001` | Override account number |
| `X-Dev-Username` | `user_dev` | Override username |
| `X-Dev-Email` | `{username}@foo.com` | Override email |
| `X-Dev-User-Id` | *(derived from username)* | Override user ID (auto-generated if omitted) |
| `X-Dev-Is-Org-Admin` | `true` | `true` or `false` |
| `X-Dev-Is-Internal` | `true` | `true` or `false` |

> **Note:** `X-Dev-User-Id` is automatically derived from the username via a hash, so each unique username gets a unique user ID. You only need to set this header if you want a specific numeric ID.

### Request Examples

```bash
# Default dev user (org admin, org_id=11111)
curl http://localhost:8080/api/rbac/v1/roles/

# As org admin in test_org_01
curl -H "X-Dev-Org-Id: test_org_01" \
     -H "X-Dev-Username: org_admin_test_org_01" \
     http://localhost:8080/api/rbac/v1/roles/

# As non-admin user in test_org_02 (will get 403 on admin-only endpoints)
curl -H "X-Dev-Org-Id: test_org_02" \
     -H "X-Dev-Username: regular_user_test_org_02" \
     -H "X-Dev-Is-Org-Admin: false" \
     http://localhost:8080/api/rbac/v1/access/?application=rbac

# As a completely new org (auto-bootstrapped on first request)
curl -H "X-Dev-Org-Id: my_custom_org" \
     -H "X-Dev-Username: my_user" \
     http://localhost:8080/api/rbac/v1/roles/
```

---

## Pre-Provisioned Test Data

The init job creates 3 test tenants, each with 3 user personas:

| Org ID | Username | User ID | Org Admin |
|--------|----------|---------|-----------|
| `test_org_01` | `org_admin_test_org_01` | `100001` | Yes |
| `test_org_01` | `regular_user_test_org_01` | `100002` | No |
| `test_org_01` | `readonly_user_test_org_01` | `100003` | No |
| `test_org_02` | `org_admin_test_org_02` | `100101` | Yes |
| `test_org_02` | `regular_user_test_org_02` | `100102` | No |
| `test_org_02` | `readonly_user_test_org_02` | `100103` | No |
| `test_org_03` | `org_admin_test_org_03` | `100201` | Yes |
| `test_org_03` | `regular_user_test_org_03` | `100202` | No |
| `test_org_03` | `readonly_user_test_org_03` | `100203` | No |

You can also use any arbitrary `X-Dev-Org-Id` value — new tenants are auto-bootstrapped on first request via the dev middleware.

### Behavior by Role

| Persona | Can list roles? | Can list principals? | Notes |
|---------|----------------|---------------------|-------|
| `org_admin_*` | Yes | Yes | Full admin access to the org |
| `regular_user_*` | No (403) | Yes | Non-admin; only sees granted permissions |
| `readonly_user_*` | No (403) | Yes | Non-admin; only sees granted permissions |

---

## Kessel Integration (Optional)

By default, RBAC operates fully standalone with `REPLICATION_TO_RELATION_ENABLED=False`.

To enable dual-write replication to Kessel Relations API:

```bash
kubectl -n rbac-local patch configmap rbac-config --type merge \
  -p '{"data":{"REPLICATION_TO_RELATION_ENABLED":"True","RELATION_API_SERVER":"<kessel-host>:9000"}}'

# Restart the server to pick up config changes
kubectl -n rbac-local rollout restart deployment/rbac-server
kubectl -n rbac-local rollout status deployment/rbac-server --timeout=120s
```

After enabling Kessel, re-run the init job so test data is replicated:

```bash
kubectl -n rbac-local delete job rbac-init
kubectl apply -f deploy/local/init-job.yaml
kubectl -n rbac-local wait --for=condition=complete job/rbac-init --timeout=300s
```

---

## Operations

### Re-running Init (re-seed / re-provision)

```bash
kubectl -n rbac-local delete job rbac-init
kubectl apply -f deploy/local/init-job.yaml
kubectl -n rbac-local wait --for=condition=complete job/rbac-init --timeout=300s
```

The init job uses `--clean` to remove and recreate test tenants on each run. Migrations and seeds are idempotent.

### Viewing Server Logs

```bash
kubectl -n rbac-local logs -f deployment/rbac-server
```

### Checking Status

```bash
kubectl -n rbac-local get pods,svc,jobs
```

### Teardown

```bash
kubectl delete namespace rbac-local
```

This removes all resources (pods, services, PVCs, jobs) in one step.

---

## Manifest Reference

| File | What it creates |
|------|----------------|
| `namespace.yaml` | `rbac-local` namespace |
| `configmap.yaml` | `rbac-config` ConfigMap with all env vars |
| `postgres.yaml` | PostgreSQL 16 StatefulSet + Service + PVC |
| `redis.yaml` | Redis 7 Deployment + Service |
| `rbac-server.yaml` | RBAC API server Deployment + ClusterIP Service |
| `init-job.yaml` | Kubernetes Job: migrations, seeds, test data provisioning |

### Customizing the Image

If using a registry, update the `image:` field in both `rbac-server.yaml` and `init-job.yaml`:

```yaml
image: your-registry.example.com/insights-rbac:local
```

### Resource Requirements

The RBAC server requests 512Mi memory / 250m CPU, with limits of 1Gi memory / 500m CPU. PostgreSQL and Redis use minimal resources. Total cluster footprint is approximately 2Gi memory.
