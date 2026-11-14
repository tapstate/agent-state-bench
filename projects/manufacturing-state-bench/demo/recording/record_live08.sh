#!/bin/bash
# The demo's live takes against Tapstate 0.8.0 (stack/docker-compose-0.8.yml): sets up env.sh + env08.sh and
# the venv, then runs record_live.sh. Paid model runs (two Claude Code sessions on the subscription).
# Usage: recording/record_live08.sh [zh|en]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$(cd "$HERE/../.." && pwd)"
. "$P/env.sh" && . "$P/env08.sh" && . "$HOME/venv-bench/bin/activate"
exec bash "$HERE/record_live.sh" "${DAB_ROOT:-$HOME/dev/DataAgentBench-mfg}" "${1:-zh}"
