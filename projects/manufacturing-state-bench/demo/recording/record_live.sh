#!/bin/bash
# Record the demo's three live terminal segments with util-linux `script` (output + timing), at a fixed
# terminal size: setup A's run, the Tapstate side, setup C's run. Paid model runs (two sessions).
# Usage: recording/record_live.sh <dab-root>     (from demo/, with env.sh sourced)
set -u
DAB="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
DEMO="$(dirname "$HERE")"
COLS=${COLS:-136} ROWS=${ROWS:-38}
rec() {   # name command
  script -q -e --log-timing="$HERE/$1.timing" --log-out="$HERE/$1.out" \
    -c "stty cols $COLS rows $ROWS; export COLUMNS=$COLS LINES=$ROWS; $2"
}
cd "$DEMO"
rec a_run "python sales_agent.py '$DAB' --setup A --model claude-opus-5-5"
rec tapstate "bash '$HERE/tapstate_side.sh'"
rec c_run "python sales_agent.py '$DAB' --setup C --model claude-opus-5-5"
ls -t runs | grep -E '^[AC]_' | head -2 | sort > "$HERE/runs.txt"
echo "runs: $(tr '\n' ' ' < "$HERE/runs.txt")"
