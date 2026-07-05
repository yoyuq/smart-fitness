"""Focused verification: session sess_31_1782807557 archived reports."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DB = _ROOT / "backend" / "fitness.db"

conn = sqlite3.connect(str(DB))
c = conn.cursor()

c.execute(
    """
    SELECT report_id, user_id, session_id, exercise, rep_count, frames_per_rep,
           overall_score, performance_rating, stage1_ok_count, stage1_total,
           created_at
    FROM ai_coach_reports
    WHERE user_id = 31 AND session_id = 'sess_31_1782807557'
    ORDER BY created_at ASC
    """
)

rows = c.fetchall()
print(f"== user 31 / session sess_31_1782807557: {len(rows)} 份 ==\n")

hdr = f"{'#':>2}  {'created':<19}  {'k':>2}  {'score':>5}  {'rating':<6}  {'stage1':>7}  {'report_id'}"
print(hdr)
print("-" * len(hdr))
for i, r in enumerate(rows, 1):
    rid, uid, sid, ex, rc, fpr, score, rating, ok, tot, ts = r
    t = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    score_s = f"{score:.0f}" if score is not None else "-"
    rating_s = rating or "-"
    print(f"{i:>2}  {t}  {fpr:>2}  {score_s:>5}  {rating_s:<6}  {ok:>3}/{tot:<3}  {rid}")

print()
alive = [r for r in rows if r[8] and r[8] == r[9] and r[6] is not None]
dead = [r for r in rows if not (r[8] and r[8] == r[9] and r[6] is not None)]
print(f"有效报告 (stage1 全通过 + overall_score 非空): {len(alive)}")
print(f"死档 (stage1_ok=0 或 overall_score=NULL): {len(dead)}")

# Show total count across all users too
c.execute("SELECT COUNT(*) FROM ai_coach_reports")
total = c.fetchone()[0]
print(f"\n所有 user 合计: {total} 份")

conn.close()
