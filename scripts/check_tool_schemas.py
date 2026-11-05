import json
import subprocess
import time

key = ""
for line in open("/root/.hermes/profiles/main/.env"):
    if line.startswith("OPENCODE_GO_API_KEY="):
        key = line.split("=", 1)[1].strip()

payload = open("/tmp/peripheral-tools-payload.json", "rb").read()
result = subprocess.run(
    [
        "curl", "-sS", "-m", "90", "https://opencode.ai/zen/go/v1/chat/completions",
        "-H", f"Authorization: Bearer {key}",
        "-H", "Content-Type: application/json",
        "-H", f"x-opencode-session: peripheral-schema-check-{int(time.time())}",
        "--data-binary", "@-",
        "-w", "\nhttp=%{http_code}",
    ],
    input=payload,
    capture_output=True,
)
text = result.stdout.decode("utf-8", "replace")
head, _, status = text.rpartition("\nhttp=")
print("status:", status.strip())
try:
    body = json.loads(head)
except json.JSONDecodeError:
    print("raw:", text[:800])
    raise SystemExit(0)
if "error" in body:
    print("ERROR:", json.dumps(body["error"])[:500])
else:
    message = body["choices"][0]["message"]
    print("content:", (message.get("content") or "")[:300])
    print("tool_calls:", [call["function"]["name"] for call in (message.get("tool_calls") or [])])
    print("usage:", body.get("usage"))
