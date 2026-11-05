#!/usr/bin/env bash
# Prove the provider accepts the plugin's tool schemas, independent of the CLI.
set -u
KEY=$(grep '^OPENCODE_GO_API_KEY=' /root/.hermes/profiles/main/.env | cut -d= -f2-)
BASE=https://opencode.ai/zen/go/v1
python3 - "$KEY" <<'PY' > /tmp/peripheral-tools-payload.json
import json, sys
sys.path.insert(0, "/root")
from peripheral import schemas
tools = [{"type": "function", "function": s} for s in schemas.ALL]
payload = {
    "model": "deepseek-v4.1-flash",
    "messages": [{"role": "user", "content": "What tools do you have for searching my screen history? Just name them."}],
    "tools": tools,
    "max_tokens": 200,
}
print(json.dumps(payload))
PY
curl -sS -m 90 "$BASE/chat/completions" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  --data @/tmp/peripheral-tools-payload.json \
  -o /tmp/peripheral-tools-response.json -w "http=%{http_code}\n"
python3 - <<'PY'
import json
body = json.load(open("/tmp/peripheral-tools-response.json"))
if "error" in body:
    print("ERROR:", json.dumps(body["error"])[:600])
else:
    msg = body["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    print("content:", (msg.get("content") or "")[:300])
    print("tool_calls:", [c["function"]["name"] for c in calls])
    print("usage:", body.get("usage"))
PY
