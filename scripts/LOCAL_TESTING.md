# Local Cluster Testing Guide

This guide is for RBAC developers who want to validate the `deploy/local/` manifests in a local Kubernetes cluster before handing them off. This is separate from the [deployment guide for consumers](../deploy/local/README.md).

## Prerequisites

- Docker CLI (via Colima, not Docker Desktop)
- `kubectl`
- A local Kubernetes runtime (see below)

## Choosing a Local Runtime

| Runtime | Recommended? | Notes |
|---------|-------------|-------|
| **Colima with k3s** | Yes (macOS arm64) | `colima start --cpu 4 --memory 6 --kubernetes` |
| **Kind** | Conditional | Works well on Linux/amd64. Has issues on arm64 Macs with QEMU (kubelet can't parse ARM `/proc/cpuinfo`). |
| **Minikube** | Yes | `minikube start` — straightforward but heavier. |

### Colima Setup (Recommended for macOS)

```bash
# Install
brew install colima

# Start with Kubernetes enabled (4 CPU, 6GB RAM minimum)
colima start --cpu 4 --memory 6 --kubernetes

# Verify
kubectl get nodes
```

> **Important:** Colima needs at least 4 CPU / 6GB RAM. With 2 CPU / 2GB, Gunicorn workers get OOM-killed.

### Stopping / Restarting Colima

```bash
colima stop
colima start --cpu 4 --memory 6 --kubernetes
```

Kubernetes state (namespaces, pods) persists across restarts as long as you don't delete the VM.

## The Deploy Script

`scripts/local-kube-deploy.sh` automates the full lifecycle:

```bash
# Full deploy: build image, load into cluster, apply manifests, run init, port-forward
./scripts/local-kube-deploy.sh deploy

# Check status
./scripts/local-kube-deploy.sh status

# Tail server logs
./scripts/local-kube-deploy.sh logs

# Re-run init job (re-seed, re-provision test data)
./scripts/local-kube-deploy.sh reinit

# Tear down everything
./scripts/local-kube-deploy.sh teardown
```

### Options

```
--runtime <name>     Force a specific runtime (colima|kind|minikube). Auto-detected by default.
--port <port>        Local port for port-forward (default: 8080)
--kessel             Enable Kessel replication (requires --kessel-server)
--kessel-server <addr>  Kessel Relations API address (e.g. kessel-relations:9000)
--delete-cluster     (teardown only) Also delete the kind cluster
```

### What the Script Does

1. **Detects your runtime** — checks for Colima (k3s), Kind, Minikube, or any working cluster via `kubectl get nodes`
2. **Builds the Docker image** — auto-detects arm64 vs amd64 and sets `--platform` accordingly
3. **Loads the image** into your cluster's container runtime:
   - Colima: `docker save | colima ssh -- sudo ctr -n k8s.io images import -`
   - Kind: `kind load docker-image`
   - Minikube: `minikube image load`
4. **Applies manifests** in order: namespace, configmap, postgres, redis, rbac-server
5. **Waits for PostgreSQL and Redis** to pass readiness probes
6. **Runs the init job** (migrations + seeds + test data provisioning)
7. **Waits for rbac-server** to pass its readiness probe
8. **Port-forwards** to `localhost:8080`

### Full Walkthrough (from scratch)

```bash
# 1. Start a local cluster (if not already running)
colima start --cpu 4 --memory 6 --kubernetes

# 2. Deploy
./scripts/local-kube-deploy.sh deploy

# 3. In another terminal, test
curl http://localhost:8080/api/rbac/v1/status/
# {"api_version":1,"commit":"undefined"}

curl http://localhost:8080/api/rbac/v1/roles/
# {"meta":{"count":35,...}, ...}

curl -H "X-Dev-Org-Id: test_org_01" \
     -H "X-Dev-Username: org_admin_test_org_01" \
     http://localhost:8080/api/rbac/v1/roles/
# {"meta":{"count":35,...}, ...}

# 4. When done, Ctrl+C the port-forward, then:
./scripts/local-kube-deploy.sh teardown

# 5. Optionally stop Colima
colima stop
```

## Verification Checklist

After a deploy, verify these work:

```bash
# Status endpoint
curl -s http://localhost:8080/api/rbac/v1/status/
# Expected: {"api_version":1,"commit":"undefined"}

# Default dev user (org admin)
curl -s http://localhost:8080/api/rbac/v1/roles/ | python3 -c "import sys,json; print('roles:', json.load(sys.stdin)['meta']['count'])"
# Expected: roles: 35

# Org admin in a pre-provisioned tenant
curl -s -H "X-Dev-Org-Id: test_org_01" -H "X-Dev-Username: org_admin_test_org_01" \
     http://localhost:8080/api/rbac/v1/roles/ | python3 -c "import sys,json; print('roles:', json.load(sys.stdin)['meta']['count'])"
# Expected: roles: 35

# Non-admin user (should get 403 on admin endpoints)
curl -s -w "\nHTTP %{http_code}\n" \
     -H "X-Dev-Org-Id: test_org_02" \
     -H "X-Dev-Username: regular_user_test_org_02" \
     -H "X-Dev-Is-Org-Admin: false" \
     http://localhost:8080/api/rbac/v1/roles/
# Expected: HTTP 403

# Non-admin user on permitted endpoint
curl -s -H "X-Dev-Org-Id: test_org_02" \
     -H "X-Dev-Username: regular_user_test_org_02" \
     -H "X-Dev-Is-Org-Admin: false" \
     "http://localhost:8080/api/rbac/v1/principals/?usernames=regular_user_test_org_02" \
     | python3 -c "import sys,json; d=json.load(sys.stdin); print('principals:', d['meta']['count'])"
# Expected: principals: 1

# Auto-provisioned new org
curl -s -H "X-Dev-Org-Id: brand_new_org" -H "X-Dev-Username: some_user" \
     http://localhost:8080/api/rbac/v1/roles/ | python3 -c "import sys,json; print('roles:', json.load(sys.stdin)['meta']['count'])"
# Expected: roles: 35
```

## Known Issues and Gotchas

### arm64 / Apple Silicon

- **Redis**: Must use `redis:7-alpine` or newer. Redis 5.x is amd64-only and crashes on arm64.
- **Kind**: Kind clusters inside Colima's QEMU VM fail because the kubelet can't parse ARM's `/proc/cpuinfo` format. Use Colima's built-in k3s instead (`--kubernetes` flag).
- **Image platform**: The deploy script auto-detects and builds with `--platform linux/arm64`. If you build manually, include that flag.

### Resource Limits

- **Colima must have at least 4 CPU / 6GB RAM.** With less, Gunicorn workers get OOM-killed (SIGKILL) causing 500 errors.
- The RBAC server is configured for 2 Gunicorn workers with 2 threads each (via `POD_CPU_LIMIT=1` and `GUNICORN_WORKER_MULTIPLIER=2`). The default without these env vars would spawn too many workers for a resource-constrained local cluster.
- If you see `Worker (pid:NNN) was sent SIGKILL! Perhaps out of memory?` in the server logs, increase Colima's memory: `colima stop && colima start --cpu 4 --memory 8 --kubernetes`.

### Image Loading

- **Colima (k3s)**: Even though Colima shares the Docker daemon, k3s uses containerd internally. Images must be imported via `docker save | colima ssh -- sudo ctr -n k8s.io images import -`. The deploy script handles this automatically.
- If pods show `ImagePullBackOff` or `ErrImagePull`, the image wasn't loaded properly. Check with: `colima ssh -- sudo ctr -n k8s.io images ls | grep insights-rbac`.

### Init Job

- The init job uses `--clean` to remove and recreate test data on each run, so `reinit` is safe to run repeatedly.
- If the init job fails, check logs with `kubectl -n rbac-local logs job/rbac-init`.
- Migrations and seeds are idempotent. Only the test data provisioning uses `--clean`.

### Port Conflicts

- The default port-forward is `8080`. If something else uses that port, pass `--port 9090` (or any free port).
