"""ai_planner.py - AI Personal Trainer / Planner

每个用户的专属 AI 规划师, 基于 DeepSeek + 用户运动/身体数据.

能力:
- daily_summary(user_id): 今日训练总结 + 明日建议
- weekly_report(user_id): 7天/30天报告 + 趋势 + 调整建议  
- generate_plan(user_id, goal, weeks): 生成训练计划, 写入 workout_plans
- chat(user_id, message): 自由对话 (带历史 + 用户数据上下文)
- meal_suggestion(user_id): 结合训练强度的食堂搭配建议
"""
import os, json, time, sqlite3, logging
from typing import Optional, Dict, List, Any

try:
    import requests
except ImportError:
    requests = None

log = logging.getLogger("ai_planner")

# ====== Provider config (双备份: 火山 + DeepSeek) ======
VOLC_API_KEY = os.environ.get("VOLC_ARK_API_KEY", "") or os.environ.get("ARK_API_KEY", "")
VOLC_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
# Coding Plan 套餐专用 Anthropic 兼容端点 (provider 名: volc-coding)
VOLC_CODING_URL = os.environ.get(
    "VOLC_CODING_URL", "https://ark.cn-beijing.volces.com/api/coding/v1/messages")
VOLC_MODEL = os.environ.get("VOLC_MODEL", "doubao-seed-1-6-250615")  # 旗舰
VOLC_MODEL_FAST = os.environ.get("VOLC_MODEL_FAST", "doubao-1-5-lite-32k-250115")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# Qwen (阿里云百炼, OpenAI 兼容). 2026-06 评测: qwen3.7-max 计划生成质量/稳定性最佳
QWEN_API_KEY = os.environ.get("BAILIAN_API_KEY", "") or os.environ.get("DASHSCOPE_API_KEY", "")
QWEN_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen3.7-max")

# 腾讯混元 (OpenAI 兼容). hunyuan-lite 免费+1.3s, 适合短点评兜底; 勿用于 JSON 计划
HUNYUAN_API_KEY = os.environ.get("HUNYUAN_API_KEY", "")
HUNYUAN_URL = "https://api.hunyuan.cloud.tencent.com/v1/chat/completions"
HUNYUAN_MODEL = os.environ.get("HUNYUAN_MODEL", "hunyuan-lite")

# 默认优先: volc -> deepseek
PROVIDER_PRIORITY = os.environ.get("AI_PROVIDER_PRIORITY", "volc,deepseek").split(",")

TIMEOUT = int(os.environ.get("AI_TIMEOUT", "180"))
MAX_TOKENS_SUMMARY = 800
# v4 系列是推理模型, reasoning tokens 计入 max_tokens; 2500 会被思考烧光导致正文为空
MAX_TOKENS_PLAN = int(os.environ.get("AI_MAX_TOKENS_PLAN", "6000"))
MAX_TOKENS_CHAT = 1000

# 兼容旧变量名
API_KEY = DEEPSEEK_API_KEY
API_URL = DEEPSEEK_URL
MODEL = DEEPSEEK_MODEL


def is_available() -> bool:
    return bool(requests and (VOLC_API_KEY or DEEPSEEK_API_KEY))


def provider_status() -> Dict[str, Any]:
    return {
        "volc_key_set": bool(VOLC_API_KEY),
        "volc_model": VOLC_MODEL,
        "deepseek_key_set": bool(DEEPSEEK_API_KEY),
        "deepseek_model": DEEPSEEK_MODEL,
        "priority": PROVIDER_PRIORITY,
    }


def _call_volc(messages, max_tokens, temperature, model=None) -> Optional[str]:
    if not (VOLC_API_KEY and requests):
        return None
    use_model = model or VOLC_MODEL
    try:
        r = requests.post(
            VOLC_URL,
            headers={"Authorization": f"Bearer {VOLC_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": use_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            log.warning(f"Volc HTTP {r.status_code}: {r.text[:300]}")
            return None
        data = r.json()
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        log.warning(f"Volc call failed: {e}")
        return None


def _call_volc_coding(messages, max_tokens, temperature, model=None) -> Optional[str]:
    """火山方舟 Coding Plan 套餐: Anthropic 兼容端点 /api/coding/v1/messages.

    标准 chat completions 接口不计入 coding 套餐额度 (会报 AccountOverdue),
    coding 套餐必须走这里, model 用 ark-code-latest.
    """
    if not (VOLC_API_KEY and requests):
        return None
    # OpenAI 风格 messages -> Anthropic 格式: system 单独提出
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    anth_msgs = [m for m in messages if m.get("role") != "system"]
    if not anth_msgs:
        anth_msgs = [{"role": "user", "content": "."}]
    body = {
        "model": model or VOLC_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": anth_msgs,
    }
    if system_parts:
        body["system"] = "\n".join(system_parts)
    try:
        r = requests.post(
            VOLC_CODING_URL,
            headers={"x-api-key": VOLC_API_KEY, "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"},
            json=body,
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            log.warning(f"VolcCoding HTTP {r.status_code}: {r.text[:300]}")
            return None
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text").strip()
    except Exception as e:
        log.warning(f"VolcCoding call failed: {e}")
        return None


def _call_deepseek(messages, max_tokens, temperature) -> Optional[str]:
    if not (DEEPSEEK_API_KEY and requests):
        return None
    try:
        r = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            log.warning(f"DeepSeek HTTP {r.status_code}: {r.text[:300]}")
            return None
        data = r.json()
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        log.warning(f"DeepSeek call failed: {e}")
        return None


def _call_openai_compat(url, key, model, messages, max_tokens, temperature) -> Optional[str]:
    """通用 OpenAI 兼容协议调用 (qwen/hunyuan 等)."""
    if not (key and requests):
        return None
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages,
                  "temperature": temperature, "max_tokens": max_tokens},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            log.warning(f"{model} HTTP {r.status_code}: {r.text[:200]}")
            return None
        return (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        log.warning(f"{model} call failed: {e}")
        return None


_DISPATCH = {
    "volc": lambda m, mt, t: _call_volc(m, mt, t),
    "volc-coding": lambda m, mt, t: _call_volc_coding(m, mt, t),
    "deepseek": lambda m, mt, t: _call_deepseek(m, mt, t),
    "qwen": lambda m, mt, t: _call_openai_compat(QWEN_URL, QWEN_API_KEY, QWEN_MODEL, m, mt, t),
    "hunyuan": lambda m, mt, t: _call_openai_compat(HUNYUAN_URL, HUNYUAN_API_KEY, HUNYUAN_MODEL, m, mt, t),
}


def _call_llm(messages: List[Dict], max_tokens: int = 600, temperature: float = 0.6,
              prefer: Optional[str] = None, chain: Optional[str] = None) -> Optional[str]:
    """按优先级调用 provider, 失败 fallback.

    chain: 逗号分隔的精确调用链 (如 "deepseek,qwen"), 给定时只按它走;
    prefer: 指定的排第一, 其余按全局优先级兜底.
    """
    if chain:
        order = [c.strip() for c in chain.split(",") if c.strip()]
    else:
        order = list(PROVIDER_PRIORITY)
        for p in ["volc-coding", "volc", "deepseek", "qwen", "hunyuan"]:
            if p not in order:
                order.append(p)
        if prefer:
            if prefer in order:
                order.remove(prefer)
            order.insert(0, prefer)
    for prov in order:
        fn = _DISPATCH.get((prov or "").strip())
        if fn is None:
            continue
        out = fn(messages, max_tokens, temperature)
        if out:
            return out
    return None


def _ensure_coach_memory(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS coach_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT DEFAULT 'general',
            note TEXT NOT NULL,
            created_at INTEGER
        )""")
    conn.commit()


def add_coach_memory(conn: sqlite3.Connection, user_id: int, note: str, category: str = "general"):
    _ensure_coach_memory(conn)
    conn.execute("INSERT INTO coach_memory (user_id, category, note, created_at) VALUES (?,?,?,?)",
                 (user_id, category, (note or "").strip()[:200], int(time.time())))
    conn.commit()


def get_coach_memories(conn: sqlite3.Connection, user_id: int, limit: int = 10) -> List[Dict]:
    _ensure_coach_memory(conn)
    rows = conn.execute(
        "SELECT id, category, note, created_at FROM coach_memory WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit)).fetchall()
    return [{"id": r[0], "category": r[1], "note": r[2], "created_at": r[3]} for r in rows]


def _load_user_context(conn: sqlite3.Connection, user_id: int) -> Dict[str, Any]:
    """汇总一个用户的上下文 (为 LLM prompt 服务).

    注意: 真实训练链路写的是 exercise_log / sessions 表;
    旧版读 user_exercise_log / daily_summary (没人写) 导致 AI 一直看不到真实数据.
    """
    cur = conn.cursor()
    ctx: Dict[str, Any] = {"user_id": user_id}

    # 用户基础
    row = cur.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if row:
        ctx["username"] = row["username"] if isinstance(row, sqlite3.Row) else row[0]

    # 最近身体指标
    row = cur.execute(
        "SELECT weight_kg, height_cm, body_fat_pct, timestamp FROM user_body_metrics WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    if row:
        w, h, bf, ts = row
        ctx["body"] = {"weight_kg": w, "height_cm": h, "body_fat": bf, "ts": ts}
        if w and h:
            ctx["body"]["bmi"] = round(w / ((h / 100) ** 2), 1)

    # 今日运动记录 (本地零点起)
    lt = time.localtime()
    midnight = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
    rows = cur.execute(
        "SELECT exercise_type, reps, duration_s, avg_form_score FROM exercise_log "
        "WHERE user_id = ? AND created_at >= ?", (user_id, midnight)).fetchall()
    ctx["today_exercises"] = [
        {"exercise": r[0], "reps": r[1], "sets": 1, "duration_s": r[2], "avg_form": r[3]}
        for r in rows]

    # 近 14 天逐日汇总 (从 exercise_log 实时聚合)
    since14 = int(time.time()) - 14 * 86400
    rows = cur.execute(
        """SELECT date(created_at,'unixepoch','localtime') AS d,
                  COUNT(*) AS sessions, SUM(reps) AS reps,
                  SUM(duration_s) AS dur, AVG(avg_form_score) AS form
           FROM exercise_log WHERE user_id=? AND created_at>=?
           GROUP BY d ORDER BY d DESC""", (user_id, since14)).fetchall()
    ctx["weekly_summary"] = [
        {"date": r[0], "sessions": r[1], "reps": r[2] or 0,
         "minutes": round((r[3] or 0) / 60.0, 1),
         "form": round(r[4], 1) if r[4] is not None else None}
        for r in rows]

    # 连续训练天数 (streak)
    days = [r["date"] for r in ctx["weekly_summary"]]
    streak = 0
    import datetime as _dt
    cursor_day = _dt.date.today()
    dayset = set(days)
    while cursor_day.isoformat() in dayset:
        streak += 1
        cursor_day -= _dt.timedelta(days=1)
    if streak == 0 and (_dt.date.today() - _dt.timedelta(days=1)).isoformat() in dayset:
        # 今天还没练, 从昨天往前数
        cursor_day = _dt.date.today() - _dt.timedelta(days=1)
        while cursor_day.isoformat() in dayset:
            streak += 1
            cursor_day -= _dt.timedelta(days=1)
    ctx["streak_days"] = streak

    # 近 28 天分动作统计 (PB / 平均评分 → 强弱项)
    since28 = int(time.time()) - 28 * 86400
    rows = cur.execute(
        """SELECT exercise_type, COUNT(*) AS n, SUM(reps) AS total_reps,
                  MAX(reps) AS pb_reps, AVG(avg_form_score) AS avg_form
           FROM exercise_log WHERE user_id=? AND created_at>=? AND exercise_type IS NOT NULL
           GROUP BY exercise_type ORDER BY n DESC""", (user_id, since28)).fetchall()
    ctx["per_exercise"] = [
        {"exercise": r[0], "times": r[1], "total_reps": r[2] or 0, "pb_reps": r[3] or 0,
         "avg_form": round(r[4], 1) if r[4] is not None else None}
        for r in rows]

    # 当前训练计划
    rows = cur.execute(
        """SELECT name, exercises, created_at FROM workout_plans
           WHERE user_id = ? ORDER BY created_at DESC LIMIT 3""", (user_id,)).fetchall()
    plans_list = []
    for r in rows:
        try:
            ex = json.loads(r[1]) if r[1] else []
        except Exception:
            ex = []
        plans_list.append({"name": r[0], "exercises": ex[:8], "items": len(ex), "created_at": r[2]})
    ctx["plans"] = plans_list

    # 教练长期记忆 (伤病/偏好/目标等)
    try:
        ctx["coach_memory"] = get_coach_memories(conn, user_id, limit=10)
    except Exception:
        ctx["coach_memory"] = []
    return ctx


# ============ 主能力 ============

SYSTEM_PROMPT_BASE = """你是一个专业、友好的个人 AI 身体训练规划师, 名叫"克劳"。
你服务一个具体用户, 熟悉其身体指标、训练史、目标和计划。
你的风格: 简洁、接地气、以用户名称呼、用身体数据说话, 不空谈。
回复用中文, 需要时加少量表情(例🔥💪)。
避免公式化套话, 避免"作为 AI"这种自护提醒。"""


def _build_user_context_block(ctx: Dict[str, Any]) -> str:
    name = ctx.get("username", f"user_{ctx['user_id']}")
    body = ctx.get("body", {})
    body_line = "(身体数据缺失)"
    if body:
        body_line = f"体重 {body.get('weight_kg','?')}kg, 身高 {body.get('height_cm','?')}cm, BMI {body.get('bmi','?')}, 体脂 {body.get('body_fat','?')}"

    today = ctx.get("today_exercises") or []
    today_line = "(今日还没有记录)" if not today else \
        "; ".join([(f"{e['exercise']} {e['reps']}个 评分{e['avg_form']:.0f}"
                    if e.get('avg_form') else f"{e['exercise']} {e['reps']}个")
                   for e in today])

    week = ctx.get("weekly_summary") or []
    week_lines = "\n".join([
        f"  {r['date']}: {r['sessions']}次训练, {r['reps']}个, {r['minutes']}分钟"
        + (f", 评分{r['form']:.0f}" if r.get('form') is not None else "")
        for r in week]) or "  (近 14 天无记录)"

    per_ex = ctx.get("per_exercise") or []
    per_ex_lines = "\n".join([
        f"  {e['exercise']}: 练过{e['times']}次, 累计{e['total_reps']}个, 单次最佳{e['pb_reps']}个"
        + (f", 平均评分{e['avg_form']:.0f}" if e.get('avg_form') is not None else "")
        for e in per_ex]) or "  (近 28 天无分动作数据)"

    plans = ctx.get("plans") or []
    plans_line = "; ".join([f"{p['name']}({p.get('items', len(p.get('exercises', [])))}项)"
                            for p in plans[:3]]) or "(无待办计划)"

    mem = ctx.get("coach_memory") or []
    mem_lines = "\n".join([f"  [{m['category']}] {m['note']}" for m in mem]) or "  (暂无)"

    return f"""## 用户档案
姓名: {name}
身体: {body_line}
连续训练: {ctx.get('streak_days', 0)} 天

## 今日训练
{today_line}

## 近 14 天逐日记录
{week_lines}

## 近 28 天分动作统计 (含个人最佳)
{per_ex_lines}

## 当前计划
{plans_line}

## 教练档案记忆 (历史观察/伤病/偏好)
{mem_lines}
"""


def daily_summary(conn: sqlite3.Connection, user_id: int) -> Dict[str, Any]:
    """今日训练总结 + 明日建议"""
    ctx = _load_user_context(conn, user_id)
    if not ctx.get("today_exercises"):
        return {
            "ok": True,
            "summary": f"你今天还没训练哦, 不如现在开始? 根据你的 BMI {ctx.get('body',{}).get('bmi','?')}, 建议今天做组下肢 + 核心。",
            "context": ctx,
            "tomorrow_suggestion": None,
        }
    ctx_block = _build_user_context_block(ctx)
    prompt = f"""{ctx_block}

请生成今日训练总结, 包含:
1. 今日亮点 (1-2 句)
2. 需要改进的一个关键点 (对后面训练的启发)
3. 明日建议动作清单 (2-3 个动作 + reps/组数, 具体可执行)
总长度控制在 200 字以内, 分点列清楚."""
    text = _call_llm(
        [{"role": "system", "content": SYSTEM_PROMPT_BASE},
         {"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS_SUMMARY, temperature=0.5
    )
    return {
        "ok": text is not None,
        "summary": text or "AI 服务暂不可用, 请稍后重试.",
        "context": ctx,
    }

def weekly_report(conn, user_id):
    """周/月报告 + 趋势分析"""
    ctx = _load_user_context(conn, user_id)
    ctx_block = _build_user_context_block(ctx)
    prompt = f"""{ctx_block}

请生成本周训练周报, 包含:
1. 本周完成总览 (总训练量、热门动作)
2. 进步评估 (对比上周, 给出 1 个具体数字进步点)
3. 弱点诊断 (form_score 偏低的动作, 1-2 个)
4. 下周计划调整建议 (3 个具体动作 + 强度)
5. 一句激励话

格式: Markdown, 总长度 300 字内."""
    text = _call_llm(
        [{"role": "system", "content": SYSTEM_PROMPT_BASE},
         {"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS_SUMMARY, temperature=0.6
    )
    return {"ok": text is not None, "report": text or "AI 服务暂不可用", "context": ctx}


def generate_plan(conn, user_id, goal, weeks=4):
    """根据目标生成训练计划, 返回结构化 plan list"""
    ctx = _load_user_context(conn, user_id)
    ctx_block = _build_user_context_block(ctx)
    prompt = f"""{ctx_block}

用户目标: {goal}
周期: {weeks} 周

请生成一份渐进式训练计划. 输出**纯 JSON 数组**(不要 markdown, 不要解释), 每项 schema:
{{
  "week": 1,
  "day": 1,
  "exercise_type": "squat",
  "target_reps": 15,
  "target_sets": 3,
  "intensity_note": "热身组, 不到力竭"
}}

约束(必须全部满足):
- 如果用户目标中指明了每周训练天数, 每周的不同 day 数量必须严格等于该天数; 否则一周 5-6 个训练日
- 渐进 (后周 reps/sets 增加)
- 涵盖动作: squat, push_up, lunge, plank, bicep_curl, shoulder_press, jumping_jack
- 总长度不超过 {weeks * 6} 条
- intensity_note 必须针对该动作给出具体技术要点(发力位置/常见错误/器械用法), 禁止"中等强度""保留X次"这类通用模板, 禁止任意两条重复; 如果用户提到器械限制, 备注要体现该器械的具体用法
- 必须是合法 JSON, 第一个字符是 ["""
    raw = _call_llm(
        [{"role": "system", "content": SYSTEM_PROMPT_BASE + " 严格按要求只输出 JSON 数组, 不要任何额外文字."},
         {"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS_PLAN, temperature=0.4,
        # 2026-06 评测: deepseek-v4-flash 主力, qwen3.7-max 质量兜底 (勿用 hunyuan, JSON 纪律差)
        chain=os.environ.get("AI_PLAN_CHAIN", "deepseek,qwen,volc-coding"),
    )
    if not raw:
        return {"ok": False, "error": "LLM 调用失败", "plans": []}
    # 清洗
    raw = raw.strip()
    if raw.startswith("```"):
        # 剥 markdown 代码块
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
    try:
        plans = json.loads(raw)
        if not isinstance(plans, list):
            raise ValueError("not a list")
    except Exception as e:
        log.warning(f"plan JSON parse failed: {e}, raw[:200]={raw[:200]}")
        return {"ok": False, "error": f"AI 返回的格式有误: {e}", "raw": raw[:500], "plans": []}

    # 写入 workout_plans 表 (真实 schema: plan_id auto, user_id, name, exercises JSON, created_at)
    cur = conn.cursor()
    today_ts = int(time.time())
    plan_name = f"AI 计划-{goal[:20]} {weeks}周"
    try:
        cur.execute(
            "INSERT INTO workout_plans (user_id, name, exercises, created_at) VALUES (?, ?, ?, ?)",
            (user_id, plan_name, json.dumps(plans, ensure_ascii=False), today_ts)
        )
        conn.commit()
        inserted = cur.rowcount
    except Exception as e:
        log.warning(f"insert plan failed: {e}")
        inserted = 0
    return {"ok": True, "plans": plans, "inserted": inserted, "goal": goal, "plan_name": plan_name}


def chat(conn, user_id, message, history=None):
    """与 AI 规划师自由对话"""
    ctx = _load_user_context(conn, user_id)
    ctx_block = _build_user_context_block(ctx)
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT_BASE + "\n\n" + ctx_block},
    ]
    if history:
        for h in history[-10:]:  # 取近 10 条
            msgs.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    msgs.append({"role": "user", "content": message})
    text = _call_llm(msgs, max_tokens=MAX_TOKENS_CHAT, temperature=0.7)
    return {"ok": text is not None, "reply": text or "我刚才走神了, 再说一遍?", "context_used": True}


def meal_suggestion(conn, user_id):
    """结合训练强度的食堂搭配 (基于用户 USER.md: 学生食堂, 2 荤 1 素 ¥12 打底, 月预算 ≤¥900)"""
    ctx = _load_user_context(conn, user_id)
    ctx_block = _build_user_context_block(ctx)
    today = ctx.get("today_exercises") or []
    total_reps = sum(e.get("reps", 0) * e.get("sets", 1) for e in today)
    intensity = "高强度" if total_reps > 100 else ("中等强度" if total_reps > 30 else "低强度")
    prompt = f"""{ctx_block}

约束:
- 学生食堂, 不能做饭
- 每餐预算: 两荤一素 ¥12 打底, 日总 ≤¥30
- 用户爱喝高碳水饮品 (酸奶/豆浆/水果茶)
- 今日运动量: 总 reps {total_reps}, 评级 {intensity}

请给晚餐 1 个具体方案 + 加餐 1 个, 总价控制 ≤¥18. 输出 3 行内, 直接报菜名 + 价格."""
    text = _call_llm(
        [{"role": "system", "content": SYSTEM_PROMPT_BASE},
         {"role": "user", "content": prompt}],
        max_tokens=300, temperature=0.6
    )
    return {"ok": text is not None, "suggestion": text or "建议晚餐: 鸡胸肉+西兰花+米饭 ¥15", "intensity": intensity, "total_reps": total_reps}



def workout_coach_remark(exercise: str, reps: int, duration_s: float, avg_form_score=None,
                          user_context: str = "") -> str:
    """调 LLM 生成单次训练点评. 失败 fallback 到规则."""
    form_str = f"{avg_form_score:.0f}" if avg_form_score is not None else "未评估"
    prompt = (
        f"你是一个简洁的健身教练. 用户刚完成训练:\n"
        f"- 动作: {exercise}\n"
        f"- 次数: {reps}\n"
        f"- 时长: {int(duration_s)} 秒\n"
        f"- 平均姿势评分: {form_str} 分\n"
        + (f"\n用户档案: {user_context}" if user_context else "")
        + "\n\n请用 1-2 句中文 (40 字以内) 点评本次训练表现, 给出鼓励或改进建议. 不要客套话, 不要 emoji."
    )
    messages = [
        {"role": "system", "content": "你是直接的健身教练, 说话简短, 不啰嗦."},
        {"role": "user", "content": prompt},
    ]
    try:
        # max_tokens 给足: 推理模型的思考 token 计入上限, 120 会饿死正文
        text = _call_llm(messages, temperature=0.7, max_tokens=800,
                         chain=os.environ.get("AI_REMARK_CHAIN", "volc-coding,hunyuan,deepseek"))
        if text: return text.strip()
    except Exception as e:
        log.warning(f"workout_coach_remark LLM fail: {e}")
    # fallback
    if avg_form_score is not None and avg_form_score >= 85:
        return f"姿势漂亮! {exercise} {reps} 个一气呵成, 平均评分 {avg_form_score:.0f} 分, 保持这个节奏."
    elif avg_form_score is not None and avg_form_score >= 70:
        return f"完成了 {reps} 个 {exercise}, 评分 {avg_form_score:.0f} 分还有进步空间, 注意核心收紧."
    elif avg_form_score is not None:
        return f"{reps} 个完成, 但 form 评分只有 {avg_form_score:.0f}, 下次放慢节奏盯准动作要点."
    else:
        return f"完成了 {reps} 个 {exercise}, 用时 {int(duration_s)} 秒, 继续保持."


def coach_review(conn, user_id) -> Dict[str, Any]:
    """私人教练管家: 系统整合历史数据+当前计划, 输出结构化深度复盘.

    低频高质量场景, 默认链 qwen3.7-max -> deepseek -> volc-coding (AI_REVIEW_CHAIN 可覆盖).
    模型同时提炼 0-2 条长期观察自动写入 coach_memory, 供 chat/计划生成复用.
    """
    import re as _re
    ctx = _load_user_context(conn, user_id)
    ctx_block = _build_user_context_block(ctx)
    prompt = f"""{ctx_block}

你是该用户的专属私人教练. 基于以上全部数据做一次系统复盘, 输出**纯 JSON 对象**(不要 markdown):
{{
  "trend": "训练量与频率趋势分析, 给出具体数字对比",
  "balance": "动作结构平衡评估(推/拉/腿/核心), 指出缺口",
  "weakness": "评分最低或最少练的动作是什么, 怎么改",
  "adherence": "对照当前计划的执行情况评价; 无计划则建议建立",
  "next_week": ["下周可执行的调整建议, 每条含动作+组次数"],
  "encouragement": "一句个性化激励, 用数据说话",
  "memory_notes": ["0-2条值得教练长期记住的观察(伤病/瓶颈/习惯), 没有则空数组"]
}}
要求: 全部中文; 每字段不超过80字; next_week 2-4条; 数据不足的字段如实说明, 不要编造数字."""
    raw = _call_llm(
        [{"role": "system", "content": SYSTEM_PROMPT_BASE + " 严格只输出 JSON 对象, 不要任何额外文字."},
         {"role": "user", "content": prompt}],
        max_tokens=4000, temperature=0.4,
        chain=os.environ.get("AI_REVIEW_CHAIN", "qwen,deepseek,volc-coding"),
    )
    if not raw:
        return {"ok": False, "error": "AI 服务暂不可用"}
    review = None
    mm = _re.search(r"\{.*\}", raw, _re.S)
    if mm:
        try:
            review = json.loads(mm.group(0))
        except Exception:
            review = None
    if review is None:
        # 模型没按 JSON 给, 退化为整段文本
        return {"ok": True, "review": None, "review_text": raw.strip()}
    # 自动沉淀教练记忆 (去重)
    saved = []
    notes = review.pop("memory_notes", None) or []
    try:
        existing = {m["note"] for m in get_coach_memories(conn, user_id, limit=30)}
    except Exception:
        existing = set()
    for n in notes[:2]:
        if isinstance(n, str) and len(n.strip()) >= 4 and n.strip() not in existing:
            try:
                add_coach_memory(conn, user_id, n.strip(), "observation")
                saved.append(n.strip())
            except Exception as e:
                log.warning(f"save coach memory failed: {e}")
    return {"ok": True, "review": review, "memory_saved": saved}


# ============================================================
# 完整运动报告 (模式2): 绑定一次刚结束的训练 session,
# 聚合本次每个 rep 的表现 + 用户历史 + 教练记忆 → 结构化报告并落库.
# ============================================================

def ensure_workout_reports_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workout_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT,
            report_json TEXT,
            created_at INTEGER
        )""")
    conn.commit()


def _summarize_session_reps(conn, session_id):
    """聚合本次 session 的 rep_scores: 分动作统计 + 高频问题."""
    try:
        rows = conn.execute(
            "SELECT exercise, total, depth, control, symmetry, peak_angle, duration_s, feedback "
            "FROM rep_scores WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
    except Exception:
        rows = []
    if not rows:
        return {"reps": 0, "by_exercise": {}, "issues": []}
    by_ex = {}
    issues = {}
    for r in rows:
        ex = r[0] if not isinstance(r, sqlite3.Row) else r["exercise"]
        total = r[1]; depth = r[2]; control = r[3]; symmetry = r[4]; fb = r[7]
        d = by_ex.setdefault(ex, {"n": 0, "total": 0.0, "depth": 0.0, "control": 0.0, "symmetry": 0.0})
        d["n"] += 1
        d["total"] += total or 0; d["depth"] += depth or 0
        d["control"] += control or 0; d["symmetry"] += symmetry or 0
        if fb and fb != "漂亮, 标准动作!":
            for part in str(fb).split(";"):
                p = part.strip()
                if p:
                    issues[p] = issues.get(p, 0) + 1
    for ex, d in by_ex.items():
        n = max(d["n"], 1)
        d["avg_total"] = round(d["total"] / n, 1)
        d["avg_depth"] = round(d["depth"] / n, 1)
        d["avg_control"] = round(d["control"] / n, 1)
        d["avg_symmetry"] = round(d["symmetry"] / n, 1)
        for k in ("total", "depth", "control", "symmetry"):
            d.pop(k, None)
    top_issues = sorted(issues.items(), key=lambda kv: -kv[1])[:5]
    return {"reps": len(rows), "by_exercise": by_ex,
            "issues": [f"{k}（{v}次）" for k, v in top_issues]}


def workout_report(conn, user_id, session_id):
    """模式2: 对一次完整训练生成报告 (结合历史 + 记忆), 落库 workout_reports."""
    # 本次 session 基本信息
    srow = conn.execute(
        "SELECT exercise_type, total_reps, avg_form_score, start_time, end_time "
        "FROM sessions WHERE session_id=? AND user_id=?",
        (session_id, str(user_id))).fetchone()
    if not srow:
        srow = conn.execute(
            "SELECT exercise_type, total_reps, avg_form_score, start_time, end_time "
            "FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if not srow:
        return {"ok": False, "error": "找不到该训练记录"}
    ex_type, total_reps, avg_form, st, et = (srow[0], srow[1], srow[2], srow[3], srow[4])
    dur_min = round(((et or 0) - (st or 0)) / 60.0, 1) if (st and et) else None

    rep_summary = _summarize_session_reps(conn, session_id)
    ctx = _load_user_context(conn, user_id)
    ctx_block = _build_user_context_block(ctx)

    per_ex_lines = "; ".join(
        f"{ex}: {d['n']}次 均分{d['avg_total']}(深度{d['avg_depth']}/控制{d['avg_control']}/对称{d['avg_symmetry']})"
        for ex, d in rep_summary["by_exercise"].items()) or f"{ex_type} 共 {total_reps} 次"
    issues_line = ", ".join(rep_summary["issues"]) or "无明显问题"

    prompt = f"""{ctx_block}

## 本次训练数据
动作明细: {per_ex_lines}
本次总次数: {total_reps}, 平均评分: {avg_form if avg_form is not None else '未评分'}, 时长: {dur_min}分钟
本次高频问题: {issues_line}

你是该用户的专属教练. 针对**这次刚结束的训练**, 结合其历史与你的记忆, 出一份训练报告.
输出**纯 JSON 对象**(不要 markdown):
{{
  "summary": "本次训练一句话总览(动作/量/评分)",
  "highlights": "本次做得好的点(具体)",
  "problems": "本次最该改进的1-2个问题(基于高频问题)",
  "vs_history": "与近期历史对比: 进步还是退步, 用具体数字",
  "recommendations": ["下次训练的可执行建议, 每条含动作要点", "..."],
  "encouragement": "一句个性化鼓励"
}}
要求: 全中文; 每字段≤80字; recommendations 2-4条; 数据不足的字段如实说明不编造."""
    raw = _call_llm(
        [{"role": "system", "content": SYSTEM_PROMPT_BASE + " 严格只输出 JSON 对象."},
         {"role": "user", "content": prompt}],
        max_tokens=3000, temperature=0.5,
        chain=os.environ.get("AI_REPORT_CHAIN", "qwen,deepseek,volc-coding"),
    )
    report = None
    if raw:
        import re as _re
        mm = _re.search(r"\{.*\}", raw, _re.S)
        if mm:
            try:
                report = json.loads(mm.group(0))
            except Exception:
                report = None
    session_brief = {
        "exercise": ex_type, "total_reps": total_reps,
        "avg_score": avg_form, "duration_min": dur_min,
        "by_exercise": rep_summary["by_exercise"], "issues": rep_summary["issues"],
    }
    # 落库
    try:
        ensure_workout_reports_table(conn)
        conn.execute(
            "INSERT INTO workout_reports (user_id, session_id, report_json, created_at) VALUES (?,?,?,?)",
            (user_id, session_id, json.dumps({"report": report, "session": session_brief},
                                             ensure_ascii=False), int(time.time())))
        conn.commit()
    except Exception as e:
        log.warning(f"workout_report save failed: {e}")
    if report is None:
        return {"ok": True, "report": None, "report_text": (raw or "").strip(), "session": session_brief}
    return {"ok": True, "report": report, "session": session_brief}
