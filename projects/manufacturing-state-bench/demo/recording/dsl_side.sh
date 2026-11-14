#!/bin/bash
# Demo "three ways" beat, the DSL path: the pipeline as a file, checked by the CLI the way an automated
# job would (validate against the workspace's sources, then describe it). Needs TAPSTATE_CLI.
HERE="$(cd "$(dirname "$0")" && pwd)"
WS="$(mktemp -d)"
mkdir -p "$WS/source" "$WS/pipeline"
cp "$HERE/../../dab/tapstate/source/"*.tap.yml "$WS/source/"
cp "$HERE/../product_state.example.tap.yml" "$WS/pipeline/"
tapstate() { "${TAPSTATE_CLI:-tapstate}" "$@" 2> >(grep -v SLF4J >&2); }
C=$'\033[36m'; D=$'\033[2m'; R=$'\033[0m'
run() { printf '%s$ %s%s\n' "$C" "$1" "$R"; sleep 0.6; eval "$1"; echo; sleep "${2:-1.5}"; }
cd "$WS"
if [ "${DEMO_LANG:-en}" = zh ]; then echo "${D}# 管道即代码：版本控制中的 .tap.yml，由脚本或 CI 自动校验、发布${R}"; else
  echo "${D}# Pipeline as code: a .tap.yml under version control, checked and applied by a script or CI${R}"; fi
echo
run "ls pipeline/ source/" 1
run "tapstate validate ." 1.5
run "tapstate desc product_state -w ." 3
rm -rf "$WS"
