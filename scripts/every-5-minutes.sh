#!/bin/bash
# Only this launchd runner controls the WeChat desktop.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export WECHAT_MONITOR_RUNNER=launchd
exec /usr/bin/python3 "$ROOT/scripts/collect_all_groups.py"
