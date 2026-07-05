"""Spike: directly call reason_about_pose on the same rep-1 features and see raw reply."""
import json, os, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# Load backend .env into os.environ so DASHSCOPE_API_KEY etc. show up.
env_path = _ROOT / "backend" / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(_ROOT / "backend"))

from fitness_agent import vision_pipeline as vp  # type: ignore
import logging
logging.basicConfig(level=logging.INFO)

# Tiny synthetic stage-1 features + rule_summary that mimic what the pipeline hands to stage 2.
features = {
    "exercise_observed": "squat",
    "movement_phase": "bottom",
    "camera_angle": "front",
    "alignment_cues": ["knees_slight_valgus", "pelvis_tucked_back"],
    "joint_angles": {"left_knee_deg": 92, "right_knee_deg": 90, "hip_deg": 85},
    "notes": "深蹲底部，膝盖轻微内扣",
}
rule_summary = {
    "depth": 82.8, "control": 100, "symmetry": 90, "total": 90.9,
    "feedback": ["蹲太深, 注意膝盖压力"],
}
user_context = {"height_cm": 175, "weight_kg": 55, "goal": "增肌"}

print(">>> providers currently on chain:")
for p in vp._reasoning_providers():
    print(f"   {p['provider']:<30}  model={p['model']}")

print("\n>>> trying reason_about_pose ...")
res = vp.reason_about_pose(
    features=features,
    exercise="squat",
    rule_summary=rule_summary,
    user_context=user_context,
    timeout=90,
)
print("ok           :", res.get("ok"))
print("provider     :", res.get("provider"))
print("model        :", res.get("model"))
print("error        :", res.get("error"))
raw = res.get("raw_reply") or ""
print(f"raw_reply    : ({len(raw)} chars)")
print(raw[:600])
print("---")
print("analysis keys:", list((res.get("analysis") or {}).keys()))
print("tried_before :", json.dumps(res.get("tried_before") or [], ensure_ascii=False, default=str)[:500])
