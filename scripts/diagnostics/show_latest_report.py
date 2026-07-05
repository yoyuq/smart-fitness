"""Show latest ai_coach_report and detect stage2 provider."""
import json
import sqlite3
import re
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DB = _ROOT / "backend" / "fitness.db"
conn = sqlite3.connect(str(DB))
c = conn.cursor()
c.execute(
    """
    SELECT report_id, exercise, rep_count, frames_per_rep, overall_score,
           performance_rating, stage1_ok_count, stage1_total, note, created_at, report_json
    FROM ai_coach_reports
    WHERE session_id='sess_31_1782807557' AND user_id=31
    ORDER BY created_at DESC LIMIT 1
    """
)
row = c.fetchone()
rid, ex, rc, fpr, sc, rating, ok, tot, note, ts, blob = row
print(f"report_id : {rid}")
print(f"created   : {datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')}")
print(f"exercise  : {ex}  reps={rc}  frames_per_rep={fpr}")
print(f"stage1    : {ok}/{tot}")
print(f"score     : {sc}  rating={rating}")
print(f"note      : {note}")

j = json.loads(blob)
print(f"\ntop-level keys: {list(j.keys())}")
oa = j.get("overall_assessment") or {}
print(f"strengths          : {oa.get('strengths')}")
print(f"common_issues count: {len(oa.get('common_issues') or [])}")
print(f"reps_with_concern  : {oa.get('reps_with_concern')}")
print(f"confidence         : {j.get('confidence')}")

# Stage 1 providers
s1 = j.get("stage1_results") or []
prov = {r.get("provider") for r in s1}
mods = {r.get("model") for r in s1}
print(f"\nstage1 providers : {prov}")
print(f"stage1 models    : {mods}")

# Stage 2: look for provider stamps inside guidance / notes / rep_by_rep
# The pipeline currently does not stamp stage2 provider explicitly; grep for hints.
rep_notes = j.get("rep_by_rep_notes") or []
print(f"\nrep_by_rep_notes ({len(rep_notes)}):")
for n in rep_notes[:6]:
    print("  -", n)
print("  ...")

guidance = j.get("guidance") or {}
print("\nguidance.immediate_corrections:")
for g in guidance.get("immediate_corrections") or []:
    print("  -", g)
print("guidance.next_session_focus:")
for g in guidance.get("next_session_focus") or []:
    print("  -", g)
print("progression_or_regression:", guidance.get("progression_or_regression"))
print("cautions:", guidance.get("cautions"))

conn.close()
