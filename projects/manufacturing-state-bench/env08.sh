# Settings for the Tapstate 0.8.0 stack beside 0.7.0 (stack/docker-compose-0.8.yml). Source after env.sh:
#   . projects/manufacturing-state-bench/env.sh && . projects/manufacturing-state-bench/env08.sh
# The six source databases are shared with the 0.7.0 stack; only Tapstate and its store differ.
export MFG_STACK08="${MFG_STACK08:-mfg-bench08}"
export MONGO_PORT=27019 TAPSTATE_PORT=8081
export MONGO_URI="mongodb://127.0.0.1:${MONGO_PORT}/?directConnection=true"   # 0.8.0's store and views
export TAPSTATE_SERVER="127.0.0.1:${TAPSTATE_PORT}"
export TAPSTATE_CLI="${TAPSTATE_CLI08:-$HOME/tapstate-cli-0.8.0/tapstate-cli-0.8.0/bin/tapstate}"

# the 0.7.0 stack's source containers, reachable from 0.8.0 under their usual names
attach_sources() {
  for s in postgres mysql mongo-sources; do
    docker network connect --alias "$s" "${MFG_STACK08}_default" "${MFG_STACK:-mfg-bench}-$s-1" 2>/dev/null || true
  done
}
export STORE_CONTAINER="${MFG_STACK08}-mongo-1"   # 0.8.0's store, for the demo's recording scripts
export TAPSTATE_VERSION=0.8.0
