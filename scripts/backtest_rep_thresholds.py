"""backtest_rep_thresholds.py — Compare old vs new evidence-based judgments on real reps.

Runs three passes over `rep_scores.angle_series`:
  1) OLD:  legacy `_DEFAULT_COUNT_CFG` (squat down=130) simulated via a copy
  2) NEW:  current file thresholds (squat down=100, evidence-cited)
  3) RULES: new `rep_quality_rules.score_rep_quality_rules` explanations

Outputs a per-rep table plus aggregate deltas.

Usage:
  python scripts/backtest_rep_thresholds.py [--limit N] [--exercise squat]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.normpath(os.path.join(HERE, "..", "backend"))
DB = os.path.join(BACKEND, "fitness.db")
sys.path.insert(0, BACKEND)

from rep_quality_rules import score_rep_quality_rules  # noqa: E402

# Legacy squat thresholds we've moved away from (kept for backtest).
OLD_DEPTH_HI = {
    "squat": 130,          # was  down=130   → now 100
    "push_up": 90,         # unchanged
    "lunge": 100,          # unchanged
    "bicep_curl": 60,      # unchanged
    "shoulder_press": None,  # not applicable
    "jumping_jack": None,
}


def _classify_old(exercise, extremum):
    hi = OLD_DEPTH_HI.get(exercise)
    if hi is None:
        return "n/a"
    return "complete" if extremum <= hi else "incomplete"


def _classify_new(exercise, extremum, cfg_lo, cfg_hi):
    # New: 'incomplete' when we haven't reached the top of the depth window.
    return "complete" if extremum <= cfg_hi else "incomplete"


def _bottom_extremum(series, direction):
    vals = [v for v in series if v is not None]
    if not vals:
        return None
    return min(vals) if direction == "min" else max(vals)


def _cfg_for(exercise):
    from rep_quality_rules import _QUALITY_RULES  # noqa: WPS437
    return _QUALITY_RULES.get(exercise)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--exercise", default=None)
    ap.add_argument("--db", default=DB)
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}")
        return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    q = ("SELECT id, session_id, exercise, angle_series, total, duration_s, peak_angle "
         "FROM rep_scores WHERE angle_series IS NOT NULL")
    params = []
    if args.exercise:
        q += " AND exercise=?"
        params.append(args.exercise)
    q += " ORDER BY id"
    if args.limit:
        q += f" LIMIT {int(args.limit)}"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    if not rows:
        print("no rep_scores rows to backtest.")
        return 0

    diffs = {"count_flipped": 0, "same": 0}
    rules_sum = 0.0
    rules_n = 0
    print(f"{'id':>4} {'ex':<15} {'peak':>6} {'old':>10} {'new':>10} "
          f"{'rules':>6} {'label':<14} issues")
    for r in rows:
        try:
            series = json.loads(r["angle_series"])
        except Exception:
            continue
        primary = series.get("primary") or []
        direction = "min" if r["exercise"] in ("squat", "push_up", "lunge", "bicep_curl") else "max"
        extremum = _bottom_extremum(primary, direction)
        if extremum is None:
            continue
        cfg = _cfg_for(r["exercise"])
        if not cfg:
            continue
        cfg_lo, cfg_hi = cfg["depth_range"]
        old = _classify_old(r["exercise"], extremum)
        new = _classify_new(r["exercise"], extremum, cfg_lo, cfg_hi)
        if old != new and old != "n/a":
            diffs["count_flipped"] += 1
        else:
            diffs["same"] += 1
        rules_result = score_rep_quality_rules(
            r["exercise"], series,
            rep_row={"duration_s": r["duration_s"], "peak_angle": r["peak_angle"]},
        )
        rules_score = rules_result["score"] if rules_result else None
        rules_label = rules_result["label"] if rules_result else "-"
        rules_issues = ",".join(i["key"] for i in (rules_result["issues"] if rules_result else []))
        if rules_score is not None:
            rules_sum += rules_score
            rules_n += 1
        print(f"{r['id']:>4} {r['exercise']:<15} {extremum:>6.1f} {old:>10} {new:>10} "
              f"{('%.1f' % rules_score) if rules_score is not None else '   -':>6} "
              f"{rules_label:<14} {rules_issues}")

    print("\n--- summary ---")
    print(f"count_flipped (old→new differ): {diffs['count_flipped']}")
    print(f"same:                            {diffs['same']}")
    if rules_n:
        print(f"mean rules score:                {rules_sum / rules_n:.1f}  (n={rules_n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
