#!/bin/bash
# Run from any machine. The folder is this script's parent, not a fixed path.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
open -b com.tencent.xinWeChat >/dev/null 2>&1 || true
exec /usr/bin/python3 "$ROOT/scripts/collect_all_groups.py"
