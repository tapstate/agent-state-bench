#!/bin/sh
# Apply the Tapstate workspace and start its ten pipelines in dependency order, from scratch.
#
#   0. stop any of the ten pipelines already running, clearing their state, and empty their views;
#      register the MySQL connector if the server does not have it
#   1. sources; discover the six source databases (Postgres mfg_erp, mfg_plm, mfg_costing; MySQL
#      mfg_mes, mfg_procurement; MongoDB mfg_wms); apply
#   2. mfg_product_ref and the three staging pipelines that read only Postgres sources
#   3. once views.mfg_product_ref is complete: discover src_mfg_ref; the two staging pipelines that
#      attach a product to a line (mfg_sales_line, mfg_po_line)
#   4. once the five staging views are complete: discover src_mfg_stage; apply again (sizes the
#      nests from row counts); the four pipelines that build setup C's collections
#
# Then run verify_tapstate.py --wait --install. Needs $TAPSTATE_SERVER and $TAPSTATE_PASSWORD
# (env.sh) and the CLI as $TAPSTATE_CLI (default: tapstate on the PATH).
# Usage: sh run_tapstate.sh
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
WS="$HERE/tapstate"
T="${TAPSTATE_CLI:-tapstate} -c $TAPSTATE_SERVER -u admin"
MONGO="${MONGO_URI:-mongodb://127.0.0.1:27017/?directConnection=true}"

preimages() {   # record pre-images on views.<collection>...: src_mfg_ref and src_mfg_stage read them with preImage: true
  python -c "import sys; from pymongo import MongoClient
v = MongoClient(sys.argv[1])['views']
for c in sys.argv[2:]: v.command({'collMod': c, 'changeStreamPreAndPostImages': {'enabled': True}})" "$MONGO" "$@"
}
count() {   # documents in views.<collection>
  python -c "import sys; from pymongo import MongoClient; print(MongoClient(sys.argv[1])['views'][sys.argv[2]].estimated_document_count())" "$MONGO" "$1"
}
wait_for() {   # wait until views.<collection> holds <n> documents and the pipeline has not failed
  coll=$1 n=$2 pipeline=$3 start=$(date +%s)
  while [ "$(count "$coll")" -lt "$n" ]; do
    if $T status "$pipeline" 2>&1 | grep -q failed; then $T status "$pipeline"; exit 1; fi
    [ $(( $(date +%s) - start )) -gt 1800 ] && { echo "timed out waiting for views.$coll"; exit 1; }
    sleep 5
  done
  echo "views.$coll: $(count "$coll") documents"
}

PIPELINES="mfg_product_ref mfg_stock mfg_offer mfg_routing mfg_sales_line mfg_po_line mfg_products mfg_work_orders mfg_sales_orders mfg_purchase_orders"
for p in $PIPELINES; do $T stop "$p" -y >/dev/null 2>&1 || true; done
python -c "
import sys; from pymongo import MongoClient
v = MongoClient(sys.argv[1])['views']
for c in v.list_collection_names():
    if c.startswith('mfg_'): v[c].drop()" "$MONGO"
$T connectors | grep -q ' mysql ' || $T register mysql

$T apply "$WS/source"
for s in erp plm mes wms procurement costing; do $T discover-schema "src_mfg_$s" >/dev/null; done
$T apply "$WS" >/dev/null 2>&1 || true   # pipelines reading views not yet discovered are refused here
for p in mfg_product_ref mfg_stock mfg_offer mfg_routing; do $T start "$p"; done
wait_for mfg_product_ref 504 mfg_product_ref
preimages mfg_product_ref
$T discover-schema src_mfg_ref >/dev/null
$T apply "$WS" >/dev/null 2>&1 || true
for p in mfg_sales_line mfg_po_line; do $T start "$p"; done
wait_for mfg_stock 1069 mfg_stock
wait_for mfg_offer 460 mfg_offer
wait_for mfg_routing 67131 mfg_routing
wait_for mfg_sales_line 121317 mfg_sales_line
wait_for mfg_po_line 8845 mfg_po_line
preimages mfg_stock mfg_offer mfg_routing mfg_sales_line mfg_po_line
$T discover-schema src_mfg_stage >/dev/null
$T apply "$WS"
for p in mfg_products mfg_work_orders mfg_sales_orders mfg_purchase_orders; do $T start "$p"; done
echo "started; now: python verify_tapstate.py <dab-root> --wait --install"
