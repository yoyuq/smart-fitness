"""Multimodal vision tool for the Smart Fitness Agent.

Uses 火山方舟 (Volcengine Ark) vision-capable model to give a *qualitative*
analysis of a single rep's key frame. This is not a scoring engine — the
authoritative quantitative score still comes from pose_engine.FORM_RULES /
RepScorer. This tool complements those rules with human-eye style observations
(e.g. "肩胛未收紧", "手腕内扣") that pure angle rules miss.
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

from ..vision import analyze_pose_image
from ..vision_pipeline import two_stage_pose_analysis, sample_rep_frames, normalize_frame_count
from .registry import register_tool


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def _resolve_rep_image(conn, user_id: int, rep_id: int) -> Dict[str, Any]:
    if not _table_exists(conn, "rep_scores"):
        return {"ok": False, "error": "rep_scores table not created yet"}
    row = conn.execute(
        "SELECT id, session_id, exercise, peak_frame, start_frame, end_frame, feedback, clip_dir, angle_series "
        "FROM rep_scores WHERE id=?",
        (rep_id,),
    ).fetchone()
    if not row:
        return {"ok": False, "error": f"rep {rep_id} not found"}
    row_dict = {k: row[k] for k in row.keys()}
    sid = row_dict.get("session_id")
    sess_row = conn.execute(
        "SELECT user_id FROM sessions WHERE session_id=?", (sid,)
    ).fetchone() if _table_exists(conn, "sessions") else None
    if not sess_row or str(sess_row[0]) != str(user_id):
        return {"ok": False, "error": "rep does not belong to current user"}
    frame = row_dict.get("peak_frame") or row_dict.get("start_frame") or row_dict.get("end_frame")
    if not frame:
        return {"ok": False, "error": "rep has no stored frame"}
    # DB stores paths relative to backend/ (e.g. 'data\rep_frames\...'); resolve
    # to absolute so whitelist ownership check works from any CWD.
    backend_dir = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    )
    if not os.path.isabs(frame):
        frame_abs = os.path.abspath(os.path.join(backend_dir, frame))
    else:
        frame_abs = os.path.abspath(frame)
    clip_dir = row_dict.get("clip_dir")
    clip_dir_abs = None
    if clip_dir:
        clip_dir_abs = clip_dir if os.path.isabs(clip_dir) else os.path.abspath(os.path.join(backend_dir, clip_dir))
    # Parse angle_series once so callers don't have to touch the raw column
    angle_series = None
    raw_series = row_dict.get("angle_series")
    if raw_series:
        try:
            angle_series = json.loads(raw_series)
        except Exception:
            angle_series = None
    return {
        "ok": True,
        "image_path": frame_abs,
        "exercise": row_dict.get("exercise"),
        "rule_feedback": row_dict.get("feedback"),
        "clip_dir": clip_dir_abs,
        "angle_series": angle_series,
    }


@register_tool(
    name="analyze_rep_image",
    description=(
        "对指定 rep 的关键帧图像做视觉定性分析 (火山方舟多模态). 输入 rep_id, "
        "自动加载 peak_frame 并送模型; 返回结构化 findings (issues/positive/recommendations)."
        " 定量分数仍以规则/rep_scores 为准. 用于回答'我这一次动作画面上具体看得出什么问题'."
    ),
    args={"rep_id": "int required, from rep_scores.id"},
    network="restricted_llm_only",
)
def analyze_rep_image_tool(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        rep_id = int(args.get("rep_id"))
    except Exception:
        return {"ok": False, "error": "rep_id required (int)"}
    if os.environ.get("AI_AGENT_VISION_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return {"ok": False, "error": "vision analysis disabled by AI_AGENT_VISION_ENABLED"}
    resolved = _resolve_rep_image(conn, user_id, rep_id)
    if not resolved.get("ok"):
        return resolved
    result = analyze_pose_image(
        image_path=resolved["image_path"],
        exercise=resolved.get("exercise"),
        rule_feedback=resolved.get("rule_feedback"),
    )
    result["rep_id"] = rep_id
    result["image_path"] = resolved["image_path"]
    return result


@register_tool(
    name="analyze_pose_image_url",
    description=(
        "对一张公开图片 URL 做视觉定性分析 (火山方舟多模态). 只接受 http(s) URL, 不接受任意本地路径. "
        "参数: image_url 必填, exercise 可选目标动作提示, feedback 可选给模型的额外文字上下文."
    ),
    args={"image_url": "http(s) URL required", "exercise": "optional string", "feedback": "optional string"},
    network="restricted_llm_only",
)
def analyze_pose_image_url_tool(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    url = (args.get("image_url") or "").strip()
    if not url:
        return {"ok": False, "error": "image_url required"}
    if os.environ.get("AI_AGENT_VISION_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return {"ok": False, "error": "vision analysis disabled by AI_AGENT_VISION_ENABLED"}
    exercise = (args.get("exercise") or "").strip() or None
    feedback = (args.get("feedback") or "").strip() or None
    return analyze_pose_image(image_url=url, exercise=exercise, rule_feedback=feedback)


# ---------------------- Two-stage pipeline tool ----------------------


def _load_evidence_for_exercise(exercise: Optional[str]) -> List[Dict[str, Any]]:
    """Pull the relevant EVIDENCE_SOURCES entries for an exercise if available."""
    if not exercise:
        return []
    try:
        from ml_pose.pose_engine import EVIDENCE_SOURCES  # type: ignore
    except Exception:
        return []
    key_lc = exercise.lower()
    hits: List[Dict[str, Any]] = []
    for k, v in EVIDENCE_SOURCES.items():
        if key_lc in k.lower() or key_lc.split("_")[0] in k.lower():
            entry = {"key": k}
            if isinstance(v, dict):
                entry.update(v)
            else:
                entry["source"] = str(v)
            hits.append(entry)
    return hits[:8]


def _load_user_context(conn, user_id: int) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"user_id": user_id}
    try:
        row = conn.execute(
            "SELECT weight_kg, height_cm, body_fat FROM body_metrics WHERE user_id=? ORDER BY ts DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row:
            ctx["body"] = {k: row[k] for k in row.keys()}
    except Exception:
        pass
    try:
        rows = conn.execute(
            "SELECT kind, note FROM coach_memory WHERE user_id=? AND kind IN ('goal','injury','preference') LIMIT 8",
            (user_id,),
        ).fetchall()
        if rows:
            ctx["memory"] = [{"kind": r["kind"], "note": r["note"]} for r in rows]
    except Exception:
        pass
    return ctx


def _load_rule_summary(conn, rep_id: int) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT id, session_id, exercise, rep_index, total, depth, control, symmetry, peak_angle, duration_s, feedback "
        "FROM rep_scores WHERE id=?",
        (rep_id,),
    ).fetchone()
    if not row:
        return {}
    return {k: row[k] for k in row.keys()}


def _load_time_series(conn, user_id: int, rep_id: int) -> Dict[str, Any]:
    """Reuse the existing ``get_rep_analysis`` output so stage 2 sees motion,
    not just the peak frame. Falls back to empty on any error."""
    try:
        from .reps import get_rep_analysis as _get_rep_analysis
        r = _get_rep_analysis(conn, user_id, {"rep_id": rep_id})
        if not r.get("ok"):
            return {}
        return {
            "angle_series": r.get("angle_series"),
            "angle_stats": r.get("angle_stats"),
            "frames": r.get("frames"),
        }
    except Exception:
        return {}


def _load_recent_reps(conn, user_id: int, session_id: str, exercise: str, this_rep_index: int) -> list:
    """Load compact summaries of nearby reps so stage 2 can talk about trend.
    Prefer other reps from the same session; if fewer than 3, fall back to
    other recent sessions with the same exercise for this user."""
    hits = []
    try:
        rows = conn.execute(
            "SELECT rep_index, total, depth, control, symmetry, peak_angle, duration_s, feedback "
            "FROM rep_scores WHERE session_id=? AND rep_index!=? ORDER BY rep_index LIMIT 15",
            (session_id, this_rep_index),
        ).fetchall()
        hits = [{k: r[k] for k in r.keys()} for r in rows]
    except Exception:
        pass
    if len(hits) >= 5:
        return hits
    # backfill from other recent sessions of the same exercise
    try:
        rows = conn.execute(
            "SELECT rs.rep_index, rs.session_id, rs.total, rs.depth, rs.control, rs.symmetry, rs.peak_angle, rs.duration_s, rs.feedback "
            "FROM rep_scores rs JOIN sessions s ON rs.session_id=s.session_id "
            "WHERE s.user_id=? AND rs.exercise=? AND rs.session_id!=? "
            "ORDER BY s.start_time DESC LIMIT 20",
            (str(user_id), exercise, session_id),
        ).fetchall()
        for r in rows:
            hits.append({k: r[k] for k in r.keys()})
            if len(hits) >= 12:
                break
    except Exception:
        pass
    return hits


@register_tool(
    name="analyze_rep_two_stage",
    description=(
        "两阶段 rep 视觉分析: 阶段一 视觉模型(火山多模态)只提取客观姿态特征(关节角、对齐、对称、相机角度、遮挡),"
        " 阶段二 文本 LLM 把视觉特征 + 规则打分器量化分数 + 用户上下文(体重/伤病/目标) + 运动学阅参阅阀值组合, "
        "输出个性化的姿态评估、完成度分、下一 rep 可执行建议、下次训练建议以及需要警惕的风险信号. "
        "支持 frames=1/3/5/7/9 参数调控智能采帧度, 默认 5 帧(覆盖完整动作周期, 底部帧锁定在中间). "
        "帧数越大识别越细(成本与延迟上升), 1=单峰值帧兼容旧版, 5=推荐, 9=最高精度。"
        "比 analyze_rep_image 结果更结构化、可引用、可个性化."
    ),
    args={
        "rep_id": "int required, from rep_scores.id",
        "frames": "optional int 1|3|5|7|9, default 5 (or env AI_AGENT_VISION_CLIP_FRAMES)",
    },
    network="restricted_llm_only",
)
def analyze_rep_two_stage_tool(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        rep_id = int(args.get("rep_id"))
    except Exception:
        return {"ok": False, "error": "rep_id required (int)"}
    if os.environ.get("AI_AGENT_VISION_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return {"ok": False, "error": "vision analysis disabled by AI_AGENT_VISION_ENABLED"}
    # frame count: explicit arg > env override > default 5
    frames_arg = args.get("frames")
    if frames_arg is None:
        frames_arg = os.environ.get("AI_AGENT_VISION_CLIP_FRAMES")
    frame_k = normalize_frame_count(frames_arg, default=5)

    resolved = _resolve_rep_image(conn, user_id, rep_id)
    if not resolved.get("ok"):
        return resolved
    rule_summary = _load_rule_summary(conn, rep_id)
    user_ctx = _load_user_context(conn, user_id)
    exercise = resolved.get("exercise")
    evidence = _load_evidence_for_exercise(exercise)
    time_series = _load_time_series(conn, user_id, rep_id)
    history = _load_recent_reps(
        conn,
        user_id,
        rule_summary.get("session_id") or "",
        rule_summary.get("exercise") or exercise or "",
        int(rule_summary.get("rep_index") or -1),
    )
    # Smart-sample multi-frame if a clip is available and frames > 1.
    sample = sample_rep_frames(
        clip_dir=resolved.get("clip_dir"),
        angle_series=resolved.get("angle_series"),
        k=frame_k,
        fallback_frame=resolved["image_path"],
    )
    image_paths = sample.get("frames") or [resolved["image_path"]]
    result = two_stage_pose_analysis(
        image_paths=image_paths if len(image_paths) > 1 else None,
        image_path=None if len(image_paths) > 1 else image_paths[0],
        exercise=exercise,
        rule_summary=rule_summary,
        user_context=user_ctx,
        evidence_citations=evidence,
        time_series=time_series,
        history=history,
    )
    result["rep_id"] = rep_id
    result["image_path"] = resolved["image_path"]
    result["frames_used"] = {
        "requested": frame_k,
        "actual": len(image_paths),
        "indices": sample.get("indices"),
        "bottom_index": sample.get("bottom_index"),
        "source": sample.get("source"),
    }
    return result


@register_tool(
    name="vision_providers_status",
    description=(
        "返回当前背后配置的视觉提供商概况: 优先级顺序、每家默认模型、是否拿到 API key. "
        "不会泄露 key 本身. 用于回答 '现在视觉分析用的是哪家模型'."
    ),
    args={},
    network="none",
)
def vision_providers_status_tool(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    from ..vision_providers import summarize_config
    snap = summarize_config()
    snap["ok"] = True
    return snap


# ======================== Session-level (组级) analysis tool ========================


def _resolve_session_reps(conn, user_id: int, session_id: str, limit: int = 30) -> Dict[str, Any]:
    """Load all reps for a session with image paths and rule summaries."""
    if not _table_exists(conn, 'rep_scores'):
        return {'ok': False, 'error': 'rep_scores table not yet created'}
    rows = conn.execute(
        'SELECT id, rep_index, exercise, depth, control, symmetry, total, '
        'peak_angle, duration_s, feedback, peak_frame, clip_dir, angle_series, ts '
        'FROM rep_scores WHERE session_id=? ORDER BY rep_index ASC LIMIT ?',
        (session_id, limit),
    ).fetchall()
    if not rows:
        return {'ok': True, 'reps': [], 'count': 0}
    backend_dir = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
    )
    reps = []
    for r in rows:
        rd = {k: r[k] for k in r.keys()}
        frame = rd.get('peak_frame') or rd.get('start_frame') or rd.get('end_frame')
        img_path = None
        if frame:
            img_path = frame if os.path.isabs(frame) else os.path.abspath(os.path.join(backend_dir, frame))
            if not os.path.isfile(img_path):
                img_path = None
        raw_series = rd.get('angle_series')
        angle_series = None
        if raw_series:
            try:
                angle_series = json.loads(raw_series)
            except Exception:
                pass
        reps.append({
            'rep_id': rd['id'],
            'rep_index': rd['rep_index'],
            'exercise': rd.get('exercise'),
            'depth': rd.get('depth'),
            'control': rd.get('control'),
            'symmetry': rd.get('symmetry'),
            'total': rd.get('total'),
            'peak_angle': rd.get('peak_angle'),
            'duration_s': rd.get('duration_s'),
            'feedback': rd.get('feedback'),
            'image_path': img_path,
            'clip_dir': rd.get('clip_dir'),
            'angle_series': angle_series,
        })
    return {'ok': True, 'reps': reps, 'count': len(reps)}


@register_tool(
    name='analyze_session_two_stage',
    description=(
        '组级两阶段视觉分析: 把一组训练的所有 rep 关键帧逐一送视觉模型, '
        '然后文本 LLM 做综合报告, 包含逐 rep 点评(如"rep3 深蹲过深")、'
        '组内趋势、用户个性化建议。输入 session_id, 返回结构化报告。'
        '比逐个 analyze_rep_two_stage 更高效, 适合完整一组做完后调 AI 教练分析。'
    ),
    args={
        'session_id': 'str required, training session UUID',
        'frames_per_rep': 'optional int 1|3|5|7|9, default 5, not yet used here',
    },
    network='restricted_llm_only',
)
def analyze_session_two_stage_tool(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(args.get('session_id') or '').strip()
    if not session_id:
        return {'ok': False, 'error': 'session_id required'}
    if os.environ.get('AI_AGENT_VISION_ENABLED', 'true').strip().lower() in {'0', 'false', 'no', 'off'}:
        return {'ok': False, 'error': 'vision analysis disabled by AI_AGENT_VISION_ENABLED'}

    resolved = _resolve_session_reps(conn, user_id, session_id, limit=30)
    if not resolved.get('ok'):
        return resolved
    reps = resolved.get('reps') or []
    if not reps:
        return {'ok': True, 'session_id': session_id, 'reps': [], 'note': 'no reps found for this session'}
    if len(reps) > 30:
        reps = reps[:30]

    from collections import Counter
    ex_counts = Counter(r.get('exercise') for r in reps if r.get('exercise'))
    if not ex_counts:
        return {'ok': False, 'error': 'no exercise label found in session reps'}
    exercise = ex_counts.most_common(1)[0][0]

    # frames per rep: explicit arg > env override > default 1
    frames_arg = args.get('frames')
    if frames_arg is None:
        frames_arg = os.environ.get('AI_AGENT_VISION_CLIP_FRAMES')
    from ..vision_pipeline import normalize_frame_count as _norm_frames
    frame_k = _norm_frames(frames_arg, default=1)

    rep_summaries = []
    rep_image_paths: List[Optional[str]] = []
    rep_frames_meta: List[Dict[str, Any]] = []
    for r in reps:
        rep_summaries.append({
            'rep_index': r['rep_index'],
            'depth': r['depth'],
            'control': r['control'],
            'symmetry': r['symmetry'],
            'total': r['total'],
            'peak_angle': r['peak_angle'],
            'duration_s': r['duration_s'],
            'feedback': r.get('feedback') or '',
        })
        # Smart-sampling multi-frame collage when frame_k > 1
        if frame_k > 1:
            from ..vision_pipeline import sample_rep_frames as _sample
            sample = _sample(
                clip_dir=r.get('clip_dir'),
                angle_series=r.get('angle_series'),
                k=frame_k,
                fallback_frame=r.get('image_path'),
            )
            frames = sample.get('frames') or []
            if len(frames) > 1:
                collage_path = _make_collage(r['rep_index'], frames, frame_k)
                rep_image_paths.append(collage_path if collage_path else frames[0])
                rep_frames_meta.append({
                    'frame_count': len(frames),
                    'collage': bool(collage_path),
                    'source': sample.get('source'),
                })
                continue
        rep_image_paths.append(r.get('image_path'))
        rep_frames_meta.append({'frame_count': 1, 'collage': False, 'source': 'single'})

    user_ctx = _load_user_context(conn, user_id)
    evidence = _load_evidence_for_exercise(exercise)

    from ..vision_pipeline import analyze_session_two_stage as _run_session_two_stage
    result = _run_session_two_stage(
        exercise=exercise,
        rep_summaries=rep_summaries,
        rep_image_paths=rep_image_paths,
        user_context=user_ctx,
        evidence_citations=evidence,
    )
    result['session_id'] = session_id
    result['reps_count'] = len(reps)
    result['frames_per_rep'] = frame_k
    result['rep_frames_meta'] = rep_frames_meta
    return result


def _make_collage(rep_index: int, frame_paths: List[str], k: int) -> Optional[str]:
    """Stitch sampled frames into a single horizontal collage for richer vision input.

    Places frames side-by-side: frames[0] | frames[1] | ... | frames[-1].
    The bottom/peak frame (always at index k//2) stays in the middle.
    Returns the absolute path to the collage image, or None on failure.
    """
    try:
        from PIL import Image
        imgs = []
        for fp in frame_paths:
            if not fp or not os.path.isfile(fp):
                continue
            img = Image.open(fp).convert('RGB')
            imgs.append(img)
        if len(imgs) < 2:
            return None
        # Resize all to uniform height (240px)
        target_h = 240
        resized = []
        for img in imgs:
            ratio = target_h / img.height
            w = int(img.width * ratio)
            resized.append(img.resize((w, target_h), Image.LANCZOS))
        total_w = sum(img.width for img in resized)
        collage = Image.new('RGB', (total_w, target_h))
        x = 0
        for img in resized:
            collage.paste(img, (x, 0))
            x += img.width
        # Save to a temp location on disk
        collage_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data', 'collages'
        )
        os.makedirs(collage_dir, exist_ok=True)
        out_path = os.path.join(collage_dir, f'session_rep{rep_index}_k{k}.jpg')
        collage.save(out_path, 'JPEG', quality=85)
        return out_path
    except Exception as e:
        log.warning(f'_make_collage failed for rep {rep_index}: {e}')
        return None


# ---------------------------------------------------------------------------
# Agent tool: expose saved AI coach reports to fitness agent context
# ---------------------------------------------------------------------------

@register_tool(
    name='list_ai_coach_reports',
    description=(
        '列出当前用户已保存的组级 AI 教练分析报告（按时间倒序）'
        '，方便 agent 引用历史教练报告辅助分析。'
        '参数 session_id (可选) 可只看某次训练的报告，limit 默认 10。'
    ),
    args={
        'session_id': {'type': 'string', 'optional': True},
        'limit': {'type': 'integer', 'optional': True, 'default': 10},
    },
)
def list_ai_coach_reports_tool(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    session_id = (args or {}).get('session_id')
    try:
        limit = min(50, max(1, int((args or {}).get('limit') or 10)))
    except Exception:
        limit = 10
    if not _table_exists(conn, 'ai_coach_reports'):
        return {'ok': True, 'reports': [], 'note': 'ai_coach_reports table not created yet'}
    if session_id:
        rows = conn.execute(
            '''SELECT report_id, session_id, exercise, rep_count, frames_per_rep,
                      overall_score, performance_rating, stage1_ok_count, stage1_total,
                      note, created_at
               FROM ai_coach_reports WHERE user_id=? AND session_id=?
               ORDER BY created_at DESC LIMIT ?''',
            (user_id, session_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            '''SELECT report_id, session_id, exercise, rep_count, frames_per_rep,
                      overall_score, performance_rating, stage1_ok_count, stage1_total,
                      note, created_at
               FROM ai_coach_reports WHERE user_id=?
               ORDER BY created_at DESC LIMIT ?''',
            (user_id, limit),
        ).fetchall()
    reports = []
    for r in rows:
        d = dict(r) if hasattr(r, 'keys') else {
            'report_id': r[0], 'session_id': r[1], 'exercise': r[2],
            'rep_count': r[3], 'frames_per_rep': r[4], 'overall_score': r[5],
            'performance_rating': r[6], 'stage1_ok_count': r[7],
            'stage1_total': r[8], 'note': r[9], 'created_at': r[10],
        }
        reports.append(d)
    return {'ok': True, 'count': len(reports), 'reports': reports}


@register_tool(
    name='get_ai_coach_report',
    description=(
        '根据 report_id 获取一份完整的已保存 AI 教练报告'
        '（包含总体评估、逐组 notes、指导建议等），'
        '供 agent 在分析用户现状时引用以前的教练反馈。'
    ),
    args={
        'report_id': {'type': 'string'},
    },
)
def get_ai_coach_report_tool(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    report_id = (args or {}).get('report_id')
    if not report_id:
        return {'ok': False, 'error': 'report_id 必填'}
    if not _table_exists(conn, 'ai_coach_reports'):
        return {'ok': False, 'error': 'ai_coach_reports table not created yet'}
    row = conn.execute(
        'SELECT * FROM ai_coach_reports WHERE report_id=? AND user_id=?',
        (report_id, user_id),
    ).fetchone()
    if not row:
        return {'ok': False, 'error': 'not_found'}
    d = dict(row) if hasattr(row, 'keys') else {}
    try:
        if d.get('report_json'):
            d['report'] = json.loads(d['report_json'])
            d.pop('report_json', None)
    except Exception:
        pass
    d['ok'] = True
    return d

