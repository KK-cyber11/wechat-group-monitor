// OpenClaw trigger body, not a standalone Node.js program.
// Set WECHAT_MONITOR_ROOT in the execution environment for a different checkout.
const res = await exec({
  command: '/usr/bin/python3 "${WECHAT_MONITOR_ROOT:-$HOME/.openclaw/workspace/projects/wechat-group-monitor}/scripts/collection_store.py" due'
});
const data = JSON.parse(String(res.aggregated || res.stdout || res.text || '').trim());
json({
  fire: !data.empty,
  message: 'Follow scripts/condense-captures.md in WECHAT_MONITOR_ROOT (default: ~/.openclaw/workspace/projects/wechat-group-monitor). Use saved text only. Never operate WeChat.'
});
