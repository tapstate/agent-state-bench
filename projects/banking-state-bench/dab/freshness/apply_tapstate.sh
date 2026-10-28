#!/usr/bin/env bash
# Apply the bank pipeline with the live core banking source (FRESHNESS.md §4): the shared
# definitions in dab/tapstate, with src_bank_cbs replaced by freshness/tapstate/src_bank_cbs.tap.yml.
# Then rebuild the view from a fresh snapshot.
# Needs TAPSTATE_PASSWORD in the environment. Usage: apply_tapstate.sh [server, default 127.0.0.1:8080]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
server="${1:-127.0.0.1:8080}"
tapstate="${TAPSTATE_BIN:-$HOME/tapstate-cli/tapstate}"
ws="$(mktemp -d)"
trap 'rm -rf "$ws"' EXIT
mkdir -p "$ws/source" "$ws/pipeline"
cp "$here"/../tapstate/source/*.tap.yml "$ws/source/"
cp "$here/tapstate/src_bank_cbs.tap.yml" "$ws/source/src_bank_cbs.tap.yml"
cp "$here"/../tapstate/pipeline/bank_customer_state.tap.yml "$ws/pipeline/"
t() { "$tapstate" -c "$server" -u admin "$@"; }
t stop -y bank_customer_state || true
# a stop is asynchronous, and apply can't refresh the pipeline's models while it is still running
for _ in $(seq 1 60); do
  t status bank_customer_state 2>/dev/null | head -1 | grep -qE '\b(stopped|failed|idle)\b' && break
  sleep 2
done
t apply "$ws"
t discover-schema src_bank_cbs > /dev/null
"${PYTHON:-python}" -c "
import sys; sys.path.insert(0, '$here')
from fresh_common import VIEW, mongo
mongo()[VIEW[0]].drop_collection(VIEW[1])"
t start bank_customer_state
