
export LANG=en_US.utf-8
export LC_ALL=en_US.utf-8

#
# Build PR image
#

IMAGE="quay.io/cloudservices/rbac"
IMAGE_TAG=$(git rev-parse --short=7 HEAD)
DOCKER_CONF="$PWD/.docker"
mkdir -p "$DOCKER_CONF"
docker --config="$DOCKER_CONF" login -u="$QUAY_USER" -p="$QUAY_TOKEN" quay.io
docker --config="$DOCKER_CONF" login -u="$RH_REGISTRY_USER" -p="$RH_REGISTRY_TOKEN" registry.redhat.io
docker --config="$DOCKER_CONF" build -t "${IMAGE}:${IMAGE_TAG}" .
docker --config="$DOCKER_CONF" push "${IMAGE}:${IMAGE_TAG}"

#
# Install Bonfire and dev virtualenv
#

if [ ! -d bonfire ]; then
    git clone https://github.com/RedHatInsights/bonfire.git
fi

if [ ! -d venv ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip setuptools wheel pipenv tox psycopg2-binary
pip install ./bonfire

#
# Deploy ClowdApp to get DB instance
#

NAMESPACE=$(bonfire namespace reserve)
oc project $NAMESPACE

cat << EOF > config.yaml
envName: env-$NAMESPACE
apps:
- name: rbac
  host: local
  repo: $PWD
  path: deploy/rbac-clowdapp.yml
  parameters:
    IMAGE: $IMAGE
EOF

bonfire config get -l -a rbac | oc apply -f -
sleep 5

#
# Grab DB creds
#

oc rollout status -w deployment/rbac-clowder-db

oc get secret rbac-clowder -o json | jq -r '.data["cdappconfig.json"]' | base64 -d | jq .database > db-creds.json

export DATABASE_NAME=$(jq -r .name < db-creds.json)
export PGPASSWORD=$(jq -r .adminPassword < db-creds.json)
export DATABASE_HOST=localhost
export DATABASE_PORT=34567
export DATABASE_USER=postgres
export DATABASE_PASSWORD=$PGPASSWORD

echo "DB Name === ${DATABASE_NAME}"

oc port-forward svc/rbac-clowder-db 34567:5432 &

pid=$!

tox -r

result=$?

kill $pid
bonfire namespace release $NAMESPACE

if [ $result != 0 ]; then
    exit $result
else
    # Need to make a dummy results file to make tests pass
    mkdir -p artifacts
    cat << EOF > artifacts/junit-dummy.xml
    <testsuite tests="1">
        <testcase classname="dummy" name="dummytest"/>
    </testsuite>
EOF
fi
