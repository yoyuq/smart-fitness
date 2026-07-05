"""Rep-level read tools for Smart Fitness Agent.

These expose per-rep scoring data (depth/control/symmetry/feedback/angle series)
that the base workout tools don't surface. Only reads; never writes.
"""
import json
import time
from typing import Any, Dict, List, Optional

from .base import clamp_int, rows_to_dicts
from .registry import register_tool


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def _user_owns_session(conn, user_id: int, session_id: str) -> bool:
    if not session_id or not _table_exists(conn, "sessions"):
        return False
    row = conn.execute(
        "SELECT user_id FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if not row:
        return False
    try:
        return int(row[0]) == int(user_id)
    except Exception:
        return False


def _user_owns_rep(conn, user_id: int, rep_row) -> bool:
    if not rep_row:
        return False
    sid = rep_row["session_id"] if isinstance(rep_row, dict) else rep_row["session_id"]
    return _user_owns_session(conn, user_id, sid)


def _shrink_angle_series(raw: Optional[str], max_points: int = 30) -> Optional[Dict[str, Any]]:
    """Store angle series is a JSON dict like {"knee_L":[...],"knee_R":[...],...}.

    We hand back only a downsampled version so the model gets shape/peak/valley
    without exhausting the context window.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    out: Dict[str, Any] = {}
    for key, series in data.items():
        if not isinstance(series, list) or not series:
            continue
        floats: List[float] = []
        for v in series:
            try:
                floats.append(float(v))
            except Exception:
                continue
        if not floats:
            continue
        n = len(floats)
        if n <= max_points:
            sampled = floats
        else:
            step = max(1, n // max_points)
            sampled = floats[::step][:max_points]
        out[key] = {
            "n": n,
            "min": round(min(floats), 1),
            "max": round(max(floats), 1),
            "mean": round(sum(floats) / n, 1),
            "sampled": [round(v, 1) for v in sampled],
        }
    return out or None


@register_tool(
    name="get_session_rep_scores",
    description=(
        "读取指定 session 的每个 rep 分项打分（深度 depth / 控制 control / 对称 symmetry / 总分 total / "
        "峰值角 peak_angle / 时长 duration_s / 规则反馈 feedback）。参数: session_id 必填。"
        "只返回属于当前用户的 session，用于回答'这次训练动作到底哪里不好'。"
    ),
    args={"session_id": "string required", "limit": "int optional, 1-200"},
)
def get_session_rep_scores(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(args.get("session_id") or "").strip()
    if not session_id:
        return {"ok": False, "error": "session_id required"}
    if not _table_exists(conn, "rep_scores"):
        return {"ok": True, "session_id": session_id, "reps": [], "note": "rep_scores table not yet created"}
    if not _user_owns_session(conn, user_id, session_id):
        return {"ok": False, "error": "session not found or does not belong to current user"}
    limit = clamp_int(args.get("limit"), 60, 1, 200)
    rows = conn.execute(
        """
        SELECT id, rep_index, exercise, depth, control, symmetry, total,
               peak_angle, duration_s, feedback, ts
        FROM rep_scores
        WHERE session_id=?
        ORDER BY rep_index ASC
        LIMIT ?
        """,
        (session_id, limit),
    ).fetchall()
    reps = rows_to_dicts(rows)
    if not reps:
        return {"ok": True, "session_id": session_id, "reps": []}

    # aggregate summary by exercise
    by_ex: Dict[str, Dict[str, Any]] = {}
    for r in reps:
        ex = r.get("exercise") or "unknown"
        agg = by_ex.setdefault(
            ex,
            {"n": 0, "total_sum": 0.0, "depth_sum": 0.0, "control_sum": 0.0, "symmetry_sum": 0.0, "min_total": 100.0, "max_total": 0.0},
        )
        agg["n"] += 1
        agg["total_sum"] += float(r.get("total") or 0)
        agg["depth_sum"] += float(r.get("depth") or 0)
        agg["control_sum"] += float(r.get("control") or 0)
        agg["symmetry_sum"] += float(r.get("symmetry") or 0)
        agg["min_total"] = min(agg["min_total"], float(r.get("total") or 100))
        agg["max_total"] = max(agg["max_total"], float(r.get("total") or 0))

    summary = []
    for ex, agg in by_ex.items():
        n = max(1, agg["n"])
        summary.append({
            "exercise": ex,
            "reps": agg["n"],
            "avg_total": round(agg["total_sum"] / n, 1),
            "avg_depth": round(agg["depth_sum"] / n, 1),
            "avg_control": round(agg["control_sum"] / n, 1),
            "avg_symmetry": round(agg["symmetry_sum"] / n, 1),
            "min_total": round(agg["min_total"], 1),
            "max_total": round(agg["max_total"], 1),
        })
    return {"ok": True, "session_id": session_id, "reps": reps, "summary_by_exercise": summary}


@register_tool(
    name="get_rep_analysis",
    description=(
        "读取单个 rep 的详细数据: 分项打分 + 峰值角度 + 关节角时间序列 (每关节最多 30 个采样点 + min/max/mean)。"
        "用于回答'这一次动作到底出了什么问题'。参数: rep_id 必填。"
    ),
    args={"rep_id": "int required"},
)
def get_rep_analysis(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        rep_id = int(args.get("rep_id"))
    except Exception:
        return {"ok": False, "error": "rep_id required (int)"}
    if not _table_exists(conn, "rep_scores"):
        return {"ok": False, "error": "rep_scores table not yet created"}
    row = conn.execute(
        """
        SELECT id, session_id, rep_index, exercise, depth, control, symmetry, total,
               peak_angle, duration_s, feedback, ts, angle_series
        FROM rep_scores
        WHERE id=?
        """,
        (rep_id,),
    ).fetchone()
    if not row:
        return {"ok": False, "error": f"rep {rep_id} not found"}
    row_dict = {k: row[k] for k in row.keys()}
    if not _user_owns_session(conn, user_id, row_dict.get("session_id")):
        return {"ok": False, "error": "rep does not belong to current user"}
    angle_series = _shrink_angle_series(row_dict.pop("angle_series", None))
    row_dict["angle_series"] = angle_series
    return {"ok": True, "rep": row_dict}


@register_tool(
    name="get_scoring_evidence",
    description=(
        "查询某个动作打分规则阈值背后的同行评审科学依据 (Escamilla, Dhahbi, Pedrosa 等 PubMed 论文). "
        "参数: exercise 可选 (squat/push_up/plank/lunge/jumping_jack/bicep_curl/shoulder_press), "
        "不传则返回全部。每条包含 claim/authors/url，支持 Agent 引用正式文献。"
    ),
    args={"exercise": "optional string; one of squat/push_up/plank/lunge/jumping_jack/bicep_curl/shoulder_press"},
)
def get_scoring_evidence(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        import sys as _sys, os as _os
        _ml = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "..", "ml_pose"))
        if _ml not in _sys.path:
            _sys.path.insert(0, _ml)
        from pose_engine import EVIDENCE_SOURCES  # type: ignore
    except Exception as exc:
        return {"ok": False, "error": f"failed to load EVIDENCE_SOURCES: {exc}"}
    exercise = (args.get("exercise") or "").strip().lower() or None
    if exercise:
        matched = {k: v for k, v in EVIDENCE_SOURCES.items() if k.startswith(exercise + ".")}
        if not matched:
            return {"ok": False, "error": f"no evidence for exercise: {exercise}", "known": sorted({k.split('.')[0] for k in EVIDENCE_SOURCES.keys()})}
        return {"ok": True, "exercise": exercise, "evidence": matched}
    return {"ok": True, "evidence": EVIDENCE_SOURCES}


@register_tool(
    name="get_last_training_analysis",
    description=(
        "读取最近 N 次训练 session 的 rep 分项汇总: 每个 session 的 total/depth/control/symmetry 平均值 + 高频反馈。"
        "用于回答'我的动作最近趋势如何'。参数: days 1-90 (默认 14), exercise 可选。"
    ),
    args={"days": "int optional", "exercise": "string optional", "limit": "int optional, 1-20"},
)
def get_last_training_analysis(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    if not _table_exists(conn, "rep_scores") or not _table_exists(conn, "sessions"):
        return {"ok": True, "sessions": [], "note": "rep_scores or sessions table not yet created"}
    days = clamp_int(args.get("days"), 14, 1, 90)
    limit = clamp_int(args.get("limit"), 8, 1, 20)
    exercise = (args.get("exercise") or "").strip() or None
    since = int(time.time()) - days * 86400

    q = (
        "SELECT session_id, exercise_type, start_time, end_time, total_reps, avg_form_score "
        "FROM sessions WHERE user_id=? AND start_time>=?"
    )
    params: List[Any] = [str(user_id), since]
    if exercise:
        q += " AND exercise_type=?"
        params.append(exercise)
    q += " ORDER BY start_time DESC LIMIT ?"
    params.append(limit)
    sess_rows = conn.execute(q, params).fetchall()
    sessions_out: List[Dict[str, Any]] = []
    for s in sess_rows:
        sid = s["session_id"]
        rep_rows = conn.execute(
            "SELECT total, depth, control, symmetry, feedback, exercise "
            "FROM rep_scores WHERE session_id=? ORDER BY rep_index",
            (sid,),
        ).fetchall()
        n = len(rep_rows)
        if n == 0:
            sessions_out.append({
                "session_id": sid,
                "exercise_type": s["exercise_type"],
                "start_time": s["start_time"],
                "total_reps": s["total_reps"],
                "avg_form_score": s["avg_form_score"],
                "rep_analysis": None,
            })
            continue
        totals = [float(r["total"] or 0) for r in rep_rows]
        depths = [float(r["depth"] or 0) for r in rep_rows]
        controls = [float(r["control"] or 0) for r in rep_rows]
        symmetries = [float(r["symmetry"] or 0) for r in rep_rows]
        feedbacks: Dict[str, int] = {}
        for r in rep_rows:
            fb = (r["feedback"] or "").strip()
            if not fb or fb == "标准!":
                continue
            for token in fb.split(";"):
                key = token.strip()[:60]
                if key:
                    feedbacks[key] = feedbacks.get(key, 0) + 1
        top_fb = sorted(feedbacks.items(), key=lambda kv: kv[1], reverse=True)[:3]
        sessions_out.append({
            "session_id": sid,
            "exercise_type": s["exercise_type"],
            "start_time": s["start_time"],
            "total_reps": s["total_reps"],
            "avg_form_score": s["avg_form_score"],
            "rep_analysis": {
                "reps_scored": n,
                "avg_total": round(sum(totals) / n, 1),
                "avg_depth": round(sum(depths) / n, 1),
                "avg_control": round(sum(controls) / n, 1),
                "avg_symmetry": round(sum(symmetries) / n, 1),
                "min_total": round(min(totals), 1) if totals else None,
                "max_total": round(max(totals), 1) if totals else None,
                "top_issues": [{"issue": k, "count": c} for k, c in top_fb],
            },
        })
    return {"ok": True, "days": days, "exercise": exercise, "sessions": sessions_out}
