#!/bin/bash
set -euo pipefail

# Local Kubernetes deployment script for validating RBAC kube manifests.
# This is for testing the deploy/local/ manifests before handing them off.
# Supports Colima (k3s), Kind, Minikube, or any cluster reachable via kubectl.
#
# See scripts/LOCAL_TESTING.md for full usage guide and troubleshooting.
# See deploy/local/README.md for the cluster-agnostic deployment guide.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFESTS_DIR="$PROJECT_ROOT/deploy/local"
NAMESPACE="rbac-local"
IMAGE_NAME="insights-rbac"
IMAGE_TAG="local"
FULL_IMAGE="$IMAGE_NAME:$IMAGE_TAG"
KIND_CLUSTER_NAME="rbac-test"

usage() {
    echo "Usage: $0 [command] [options]"
    echo ""
    echo "Commands:"
    echo "  deploy     Create cluster (if needed), build image, apply manifests, run init, port-forward (default)"
    echo "  teardown   Remove all resources in the $NAMESPACE namespace"
    echo "  status     Show pod/service status"
    echo "  logs       Tail rbac-server logs"
    echo "  reinit     Delete and re-run the init job"
    echo ""
    echo "Options:"
    echo "  --runtime         colima|kind|minikube  (auto-detected if omitted)"
    echo "  --port            Local port for port-forward (default: 8080)"
    echo "  --kessel          Enable Kessel replication (requires --kessel-server)"
    echo "  --kessel-server   Kessel Relations API server address (e.g. kessel-relations:9000)"
    echo "  --delete-cluster  (teardown only) Also delete the kind cluster"
}

detect_runtime() {
    # Check colima first (outputs to stderr)
    if command -v colima &>/dev/null && colima status 2>&1 | grep -q "kubernetes: enabled"; then
        echo "colima"
    elif command -v kind &>/dev/null && kind get clusters 2>/dev/null | grep -q "^${KIND_CLUSTER_NAME}$"; then
        echo "kind"
    elif command -v minikube &>/dev/null && minikube status --format='{{.Host}}' 2>/dev/null | grep -q Running; then
        echo "minikube"
    elif kubectl get nodes &>/dev/null; then
        # Generic working cluster
        echo "cluster"
    else
        echo ""
    fi
}

ensure_cluster() {
    local runtime
    runtime=$(detect_runtime)

    if [ -n "$runtime" ]; then
        echo >&2 "==> Found existing $runtime cluster"
        echo "$runtime"
        return
    fi

    echo >&2 "Error: No running Kubernetes cluster found."
    echo >&2 ""
    echo >&2 "Options to set one up:"
    echo >&2 "  Colima (recommended on macOS): colima start --cpu 4 --memory 6 --kubernetes"
    echo >&2 "  Kind:                          kind create cluster --name $KIND_CLUSTER_NAME"
    echo >&2 "  Minikube:                      minikube start"
    exit 1
}

build_image() {
    echo "==> Building container image: $FULL_IMAGE"
    # Detect architecture for the build platform
    local arch
    arch=$(docker info --format '{{.Architecture}}' 2>/dev/null)
    case "$arch" in
        aarch64|arm64) platform="linux/arm64" ;;
        *)             platform="linux/amd64" ;;
    esac
    docker build --platform "$platform" -t "$FULL_IMAGE" "$PROJECT_ROOT"
}

load_image() {
    local runtime=$1
    echo "==> Loading image into $runtime cluster"
    case "$runtime" in
        kind)
            kind load docker-image "$FULL_IMAGE" --name "$KIND_CLUSTER_NAME"
            ;;
        minikube)
            minikube image load "$FULL_IMAGE"
            ;;
        colima)
            # Colima's k3s uses the same Docker daemon, but k3s uses containerd.
            # Import from Docker into k3s's containerd.
            docker save "$FULL_IMAGE" | colima ssh -- sudo ctr -n k8s.io images import -
            ;;
        *)
            echo "Warning: Unknown runtime '$runtime', skipping image load."
            echo "Make sure the image '$FULL_IMAGE' is available in your cluster."
            ;;
    esac
}

apply_manifests() {
    echo "==> Applying manifests from $MANIFESTS_DIR"
    kubectl apply -f "$MANIFESTS_DIR/namespace.yaml"
    kubectl apply -f "$MANIFESTS_DIR/configmap.yaml"
    kubectl apply -f "$MANIFESTS_DIR/postgres.yaml"
    kubectl apply -f "$MANIFESTS_DIR/redis.yaml"
    kubectl apply -f "$MANIFESTS_DIR/rbac-server.yaml"
}

wait_for_deps() {
    echo "==> Waiting for PostgreSQL to be ready..."
    kubectl -n "$NAMESPACE" wait --for=condition=ready pod -l app=rbac-db --timeout=120s

    echo "==> Waiting for Redis to be ready..."
    kubectl -n "$NAMESPACE" wait --for=condition=ready pod -l app=rbac-redis --timeout=60s
}

run_init_job() {
    echo "==> Deleting previous init job (if any)..."
    kubectl -n "$NAMESPACE" delete job rbac-init --ignore-not-found=true

    echo "==> Running init job (migrations + seeds + test data)..."
    kubectl apply -f "$MANIFESTS_DIR/init-job.yaml"
    kubectl -n "$NAMESPACE" wait --for=condition=complete job/rbac-init --timeout=300s

    echo "==> Init job logs:"
    kubectl -n "$NAMESPACE" logs job/rbac-init
}

wait_for_server() {
    echo "==> Waiting for rbac-server to be ready..."
    kubectl -n "$NAMESPACE" wait --for=condition=ready pod -l app=rbac-server --timeout=120s
}

apply_kessel_config() {
    local kessel_server=$1
    echo "==> Enabling Kessel replication (server: $kessel_server)"
    kubectl -n "$NAMESPACE" patch configmap rbac-config --type merge \
        -p "{\"data\":{\"REPLICATION_TO_RELATION_ENABLED\":\"True\",\"RELATION_API_SERVER\":\"$kessel_server\"}}"
    # Restart server to pick up config change
    kubectl -n "$NAMESPACE" rollout restart deployment/rbac-server
    kubectl -n "$NAMESPACE" rollout status deployment/rbac-server --timeout=120s
}

port_forward() {
    local port=$1
    echo ""
    echo "=========================================="
    echo "RBAC is ready!"
    echo "=========================================="
    echo ""
    echo "API base URL: http://localhost:$port/api/rbac/v1/"
    echo ""
    echo "Example requests:"
    echo "  # Default dev user (org_id=11111, org admin)"
    echo "  curl http://localhost:$port/api/rbac/v1/roles/"
    echo ""
    echo "  # As a specific test org"
    echo "  curl -H 'X-Dev-Org-Id: test_org_01' \\"
    echo "       -H 'X-Dev-Username: org_admin_test_org_01' \\"
    echo "       http://localhost:$port/api/rbac/v1/roles/"
    echo ""
    echo "  # As a non-admin user"
    echo "  curl -H 'X-Dev-Org-Id: test_org_01' \\"
    echo "       -H 'X-Dev-Username: regular_user_test_org_01' \\"
    echo "       -H 'X-Dev-Is-Org-Admin: false' \\"
    echo "       http://localhost:$port/api/rbac/v1/roles/"
    echo ""
    echo "Port-forwarding on localhost:$port (Ctrl+C to stop)..."
    echo "=========================================="
    kubectl -n "$NAMESPACE" port-forward svc/rbac-server "$port:8080"
}

do_deploy() {
    local runtime=$1
    local port=$2
    local kessel_enabled=$3
    local kessel_server=$4

    build_image
    load_image "$runtime"
    apply_manifests
    wait_for_deps
    run_init_job
    wait_for_server

    if [ "$kessel_enabled" = "true" ]; then
        apply_kessel_config "$kessel_server"
    fi

    port_forward "$port"
}

do_teardown() {
    local delete_cluster=$1

    echo "==> Tearing down $NAMESPACE namespace..."
    kubectl delete namespace "$NAMESPACE" --ignore-not-found=true
    echo "==> Namespace teardown complete."

    if [ "$delete_cluster" = "true" ]; then
        if command -v kind &>/dev/null && kind get clusters 2>/dev/null | grep -q "^${KIND_CLUSTER_NAME}$"; then
            echo "==> Deleting kind cluster '$KIND_CLUSTER_NAME'..."
            kind delete cluster --name "$KIND_CLUSTER_NAME"
            echo "==> Cluster deleted."
        elif command -v colima &>/dev/null && colima status &>/dev/null; then
            echo "==> Colima cluster detected. To stop it: colima stop"
            echo "==> To restart without Kubernetes: colima start --cpu 4 --memory 6"
        else
            echo "==> No managed cluster found to delete."
        fi
    fi
}

do_status() {
    echo "==> Pods:"
    kubectl -n "$NAMESPACE" get pods -o wide 2>/dev/null || echo "  Namespace $NAMESPACE not found"
    echo ""
    echo "==> Services:"
    kubectl -n "$NAMESPACE" get svc 2>/dev/null || true
    echo ""
    echo "==> Jobs:"
    kubectl -n "$NAMESPACE" get jobs 2>/dev/null || true
}

do_logs() {
    kubectl -n "$NAMESPACE" logs -f deployment/rbac-server
}

do_reinit() {
    run_init_job
}

# Parse arguments
COMMAND="${1:-deploy}"
shift || true

RUNTIME=""
PORT="8080"
KESSEL_ENABLED="false"
KESSEL_SERVER=""
DELETE_CLUSTER="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --runtime)
            RUNTIME="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --kessel)
            KESSEL_ENABLED="true"
            shift
            ;;
        --kessel-server)
            KESSEL_SERVER="$2"
            shift 2
            ;;
        --delete-cluster)
            DELETE_CLUSTER="true"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# For deploy: ensure a cluster exists (create kind cluster if needed)
if [ "$COMMAND" = "deploy" ]; then
    if [ -z "$RUNTIME" ]; then
        RUNTIME=$(ensure_cluster)
    fi
    echo "==> Using runtime: $RUNTIME"
fi

if [ "$KESSEL_ENABLED" = "true" ] && [ -z "$KESSEL_SERVER" ]; then
    echo "Error: --kessel requires --kessel-server to be set."
    exit 1
fi

case "$COMMAND" in
    deploy)
        do_deploy "$RUNTIME" "$PORT" "$KESSEL_ENABLED" "$KESSEL_SERVER"
        ;;
    teardown)
        do_teardown "$DELETE_CLUSTER"
        ;;
    status)
        do_status
        ;;
    logs)
        do_logs
        ;;
    reinit)
        do_reinit
        ;;
    *)
        echo "Unknown command: $COMMAND"
        usage
        exit 1
        ;;
esac
