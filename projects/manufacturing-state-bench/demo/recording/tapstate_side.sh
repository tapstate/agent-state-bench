#!/bin/bash
# Demo step 2: the Tapstate side, live. Shows each command, then runs it. Needs env.sh sourced and
# TAPSTATE_CLI pointing at the tapstate CLI (default: tapstate on the PATH). DEMO_LANG=zh prints the
# narration and the product's labels in Simplified Chinese.
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../../dab" || exit 1
tapstate() { "${TAPSTATE_CLI:-tapstate}" -c "$TAPSTATE_SERVER" -u admin "$@"; }   # signed in from env.sh
B=$'\033[1m'; C=$'\033[36m'; D=$'\033[2m'; R=$'\033[0m'
run() { printf '%s$ %s%s\n' "$C" "$1" "$R"; sleep 0.8; eval "$1"; echo; sleep "${2:-2}"; }
STORE="${STORE_CONTAINER:-mfg-bench-mongo-1}"   # Tapstate's store container (env08.sh sets the 0.8 one)
docker cp -q "$HERE/show_product.js" $STORE:/tmp/show_product.js

if [ "${DEMO_LANG:-en}" = zh ]; then
  INTRO="Tapstate 读取的正是智能体刚才查询的六个数据库，并维护一份整合后的数据。"
  DOC="mfg_state.products 中的一个文档：这款自行车，内嵌物料清单和库存（精简显示）。"
else
  INTRO="Tapstate reads the same six databases the agent just queried, and keeps one consolidated copy."
  DOC="One document in mfg_state.products: the bike, with its bill of materials and stock embedded (compact view)."
fi
echo "${B}${INTRO}${R}"
echo
sleep 2
run "head -n 20 tapstate/source/src_mfg_{erp,plm,costing}.tap.yml | grep -vE '^(version|kind):'" 4
run "head -n 20 tapstate/source/src_mfg_{mes,procurement,wms}.tap.yml | grep -vE '^(version|kind):'" 4
run "for p in mfg_products mfg_work_orders mfg_sales_orders mfg_purchase_orders; do tapstate status \$p 2>/dev/null | head -n 1; done" 3
echo "${D}${DOC}${R}"
sleep 1
if [ "${DEMO_LANG:-en}" = zh ]; then
  run "docker exec -e DEMO_LANG=zh $STORE mongosh --quiet mfg_state /tmp/show_product.js" 8
else
  run "docker exec $STORE mongosh --quiet mfg_state /tmp/show_product.js" 8
fi
