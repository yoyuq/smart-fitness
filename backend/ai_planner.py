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
VOLC_MODEL = os.environ.get("VOLC_MODEL", "doubao-seed-1-6-250615")  # 旗舰
VOLC_MODEL_FAST = os.environ.get("VOLC_MODEL_FAST", "doubao-1-5-lite-32k-250115")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# 默认优先: volc -> deepseek
PROVIDER_PRIORITY = os.environ.get("AI_PROVIDER_PRIORITY", "volc,deepseek").split(",")

TIMEOUT = 60
MAX_TOKENS_SUMMARY = 800
MAX_TOKENS_PLAN = 2500
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


def _call_llm(messages: List[Dict], max_tokens: int = 600, temperature: float = 0.6,
              prefer: Optional[str] = None) -> Optional[str]:
    """按优先级调用 provider, 失败 fallback."""
    order = [prefer] if prefer else list(PROVIDER_PRIORITY)
    if not prefer:
        # 去重 + 补上没在 priority 里的
        for p in ["volc", "deepseek"]:
            if p not in order:
                order.append(p)
    for prov in order:
        prov = (prov or "").strip()
        if prov == "volc":
            out = _call_volc(messages, max_tokens, temperature)
            if out: return out
        elif prov == "deepseek":
            out = _call_deepseek(messages, max_tokens, temperature)
            if out: return out
    return None


def _load_user_context(conn: sqlite3.Connection, user_id: int) -> Dict[str, Any]:
    """汇总一个用户的上下文 (为 LLM prompt 服务)."""
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
            bmi = round(w / ((h / 100) ** 2), 1)
            ctx["body"]["bmi"] = bmi

    # 今日运动记录 (24h)
    since = int(time.time()) - 86400
    rows = cur.execute(
        """SELECT exercise_type, reps, sets, avg_form_score
           FROM user_exercise_log
           WHERE user_id = ? AND performed_at > ?""",
        (user_id, since)
    ).fetchall()
    today_log = []
    for r in rows:
        today_log.append({
            "exercise": r[0], "reps": r[1], "sets": r[2], "avg_form": r[3]
        })
    ctx["today_exercises"] = today_log

    # 近 7 天汇总
    rows = cur.execute(
        """SELECT date, total_reps, total_calories, avg_form_score
           FROM daily_summary WHERE user_id = ?
           ORDER BY date DESC LIMIT 7""",
        (user_id,)
    ).fetchall()
    ctx["weekly_summary"] = [
        {"date": r[0], "reps": r[1], "kcal": r[2], "form": r[3]}
        for r in rows
    ]

    # 当前训练计划
    rows = cur.execute(
        """SELECT name, exercises, created_at
           FROM workout_plans WHERE user_id = ?
           ORDER BY created_at DESC LIMIT 5""",
        (user_id,)
    ).fetchall()
    plans_list = []
    for r in rows:
        try:
            ex = json.loads(r[1]) if r[1] else []
        except Exception:
            ex = []
        plans_list.append({"name": r[0], "exercises": ex[:3], "created_at": r[2]})
    ctx["plans"] = plans_list
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
        "; ".join([f"{e['exercise']} {e['reps']}个 x{e['sets']}组 评分{e['avg_form']:.0f}"
                    if e['avg_form'] else f"{e['exercise']} {e['reps']}个 x{e['sets']}组"
                    for e in today])

    week = ctx.get("weekly_summary") or []
    week_lines = "\n".join([f"  {r['date']}: {r['reps']}个, {r['kcal']}千卡, 评分{r['form'] or 0:.0f}" for r in week]) or "  (近 7 天无记录)"

    plans = ctx.get("plans") or []
    plans_line = "; ".join([f"{p['name']}({len(p.get('exercises', []))}项)" for p in plans[:5]]) or "(无待办计划)"

    return f"""## 用户档案
姓名: {name}
身体: {body_line}

## 今日训练
{today_line}

## 近 7 天汇总
{week_lines}

## 当前计划
{plans_line}
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

约束:
- 一周 5-6 个训练日, 1-2 休息
- 渐进 (后周 reps/sets 增加)
- 涵盖动作: squat, push_up, lunge, plank, bicep_curl, shoulder_press, jumping_jack
- 总长度不超过 {weeks * 6} 条
- 必须是合法 JSON, 第一个字符是 ["""
    raw = _call_llm(
        [{"role": "system", "content": SYSTEM_PROMPT_BASE + " 严格按要求只输出 JSON 数组, 不要任何额外文字."},
         {"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS_PLAN, temperature=0.4
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
        text = _call_llm(messages, temperature=0.7, max_tokens=120, prefer="deepseek")
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
