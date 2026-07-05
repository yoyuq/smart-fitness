"""Trigger a session-level AI coach analysis and dump the provider chain.

Environment variables (all optional except SMART_FITNESS_SESSION_ID):
    SMART_FITNESS_SESSION_ID   session id to re-analyze (required)
    SMART_FITNESS_USER_ID      user id owning the session (default 1)
    SMART_FITNESS_USERNAME     username, used only for token generation
    SMART_FITNESS_BACKEND_URL  backend base URL (default http://127.0.0.1:8080)
    JWT_SECRET                 must match backend's JWT secret to self-sign
    SMART_FITNESS_FRAMES_PER_REP   default 5

Example:
    set SMART_FITNESS_SESSION_ID=sess_XXX
    set SMART_FITNESS_USER_ID=1
    python scripts/diagnostics/trigger_stage2_coding_plan.py
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

SID = os.environ.get("SMART_FITNESS_SESSION_ID")
if not SID:
    raise SystemExit("Set SMART_FITNESS_SESSION_ID to the session id to re-analyze.")
UID = int(os.environ.get("SMART_FITNESS_USER_ID", "1"))
UNAME = os.environ.get("SMART_FITNESS_USERNAME", "user")
BACKEND = os.environ.get("SMART_FITNESS_BACKEND_URL", "http://127.0.0.1:8080")
FRAMES = int(os.environ.get("SMART_FITNESS_FRAMES_PER_REP", "5"))

COACH_URL = f"{BACKEND}/api/v2/training/session/{SID}/ai_coach"

# Self-sign a token using the backend's JWT_SECRET (must match server env).
sys.path.insert(0, str(_ROOT / "backend"))
try:
    from auth import generate_token  # type: ignore
except Exception as e:
    raise SystemExit(
        f"cannot import backend.auth.generate_token: {e}. "
        "Run this script from a machine where backend/ is importable and JWT_SECRET matches the running server."
    )

token = generate_token(UID, UNAME)
print(f"self-signed token for user_id={UID}")

req = urllib.request.Request(
    COACH_URL,
    data=json.dumps({"frames_per_rep": FRAMES}).encode(),
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    method="POST",
)
t0 = time.time()
with urllib.request.urlopen(req, timeout=180) as r:
    body = r.read().decode("utf-8")
dt = time.time() - t0
print(f"HTTP {r.status}  {dt:.1f}s")
try:
    payload = json.loads(body)
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
except Exception:
    print(body[:2000])
