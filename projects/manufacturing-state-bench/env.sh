# Settings shared by the stack, the build scripts and the harness. Source it in every shell:
#   . projects/manufacturing-state-bench/env.sh
# Override any value by exporting it before sourcing, e.g. PG_PORT=56432 to move Postgres.

export MFG_STACK="${MFG_STACK:-mfg-bench}"        # Compose project name of stack/docker-compose.yml
export PG_HOST="${PG_HOST:-127.0.0.1}"
export PG_PORT="${PG_PORT:-55432}"
export PG_USER="${PG_USER:-postgres}"
export PG_PASSWORD="${PG_PASSWORD:-secret}"
export MONGO_PORT="${MONGO_PORT:-27017}"
export MONGO_URI="${MONGO_URI:-mongodb://127.0.0.1:${MONGO_PORT}/?directConnection=true}"   # Tapstate's store, setup C
export MONGO_SOURCES_PORT="${MONGO_SOURCES_PORT:-27018}"
export MONGO_SOURCES_URI="${MONGO_SOURCES_URI:-mongodb://127.0.0.1:${MONGO_SOURCES_PORT}/?directConnection=true}"  # WMS
export MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
export MYSQL_PORT="${MYSQL_PORT:-33306}"
export MYSQL_USER="${MYSQL_USER:-root}"
export MYSQL_PASSWORD="${MYSQL_PASSWORD:-secret}"
export TAPSTATE_PORT="${TAPSTATE_PORT:-8080}"
export TAPSTATE_SERVER="${TAPSTATE_SERVER:-127.0.0.1:${TAPSTATE_PORT}}"
export TAPSTATE_PASSWORD="${TAPSTATE_PASSWORD:-admin}"  # the stack's first administrator

# DAB keeps server-side databases between runs instead of reloading them per run.
export DAB_PERSISTENT_DBS=1
# The harness's psql, mysql and mongorestore stand-ins (harness/bin) run inside the stack's containers.
export DAB_PG_CONTAINER="${DAB_PG_CONTAINER:-${MFG_STACK}-postgres-1}"
export DAB_MYSQL_CONTAINER="${DAB_MYSQL_CONTAINER:-${MFG_STACK}-mysql-1}"
export DAB_MONGO_NETWORK="${DAB_MONGO_NETWORK:-${MFG_STACK}_default}"
# DAB's execute_python runs in a container; the Docker Engine socket on Linux.
export DOCKER_HOST="${DOCKER_HOST:-unix:///var/run/docker.sock}"
