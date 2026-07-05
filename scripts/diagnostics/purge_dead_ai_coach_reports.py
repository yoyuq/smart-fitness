"""Purge pre-fix dead ai_coach_reports (stage1_ok=0/N) for a session.

Safe: default is dry-run; pass --apply to actually delete.
Only deletes rows where stage1_total > 0 AND stage1_ok_count == 0.
"""
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DB = _ROOT / "backend" / "fitness.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", type=int, default=31)
    ap.add_argument("--session-id", default="sess_31_1782807557")
    ap.add_argument("--apply", action="store_true", help="Actually delete")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB))
    c = conn.cursor()

    c.execute(
        """
        SELECT report_id, stage1_ok_count, stage1_total, overall_score, created_at
        FROM ai_coach_reports
        WHERE user_id = ? AND session_id = ?
              AND stage1_total > 0 AND stage1_ok_count = 0
        ORDER BY created_at ASC
        """,
        (args.user_id, args.session_id),
    )
    dead = c.fetchall()

    print(f"匹配死档 {len(dead)} 份 (user={args.user_id}, session={args.session_id}):")
    for r in dead:
        rid, ok, tot, sc, ts = r
        t = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  - {rid}  {t}  stage1={ok}/{tot}  score={sc}")

    if not dead:
        print("没有死档需要清理。")
        return

    if not args.apply:
        print("\n[dry-run] 不执行删除。加 --apply 才会真删。")
        return

    ids = [r[0] for r in dead]
    q = "DELETE FROM ai_coach_reports WHERE report_id IN (" + ",".join("?" * len(ids)) + ")"
    c.execute(q, ids)
    conn.commit()
    print(f"\n已删除 {c.rowcount} 行。")

    c.execute(
        "SELECT COUNT(*) FROM ai_coach_reports WHERE user_id = ? AND session_id = ?",
        (args.user_id, args.session_id),
    )
    print(f"该 session 现剩余报告: {c.fetchone()[0]} 份")

    conn.close()


if __name__ == "__main__":
    main()
