"""Probe which real ark model ids are reachable with the coding-plan key."""
import json, os, urllib.request, urllib.error

KEY = os.environ["VOLCANO_ENGINE_API_KEY"]
URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

# Candidates: ark model ids that coding plan is known to include (Seed 1.6 family,
# DeepSeek V3.1, Kimi K2, GLM-4.6, MiniMax M2, plus a couple ancillary aliases).
candidates = [
    "doubao-seed-1-6-250615",
    "doubao-seed-1-6-flash-250615",
    "doubao-seed-1-6-thinking-250715",
    "deepseek-v3-1-250821",
    "deepseek-v3-2-exp",
    "kimi-k2-250711",
    "glm-4-6-250910",
    "minimax-m2-241128",
]

for m in candidates:
    body = {
        "model": m,
        "messages": [{"role": "user", "content": "ok"}],
        "max_tokens": 4,
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            usage = data.get("usage", {})
            print(f"OK  {m:<38}  usage={usage}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:200]
        print(f"ERR {m:<38}  {e.code}  {body}")
    except Exception as e:
        print(f"ERR {m:<38}  {type(e).__name__}: {e}")
