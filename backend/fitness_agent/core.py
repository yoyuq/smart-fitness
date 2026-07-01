"""fitness_agent.py - 专属健身 Agent

把训练计划、运动分析、AI 私教、AI 营养师统一成一个面向当前用户的 Agent。
每个方向有独立知识库片段, 调用时按用户意图检索并拼进系统提示词。
"""
import json
import os
import re
import sqlite3
from typing import Any, Dict, List

import ai_planner


KNOWLEDGE_BASE: Dict[str, str] = {
    "nutrition": """
【营养师知识库】
- 优先估算: BMR 用 Mifflin-St Jeor 粗估; 不知道年龄/性别时给范围, 不要伪装精确。
- 蛋白质: 普通训练 1.2-1.6 g/kg/天; 增肌或训练量高 1.6-2.0 g/kg/天。
- 碳水: 长跑/速耐/高训练量 4-7 g/kg/天; 减脂期 2-4 g/kg/天, 训练日前后优先补碳水。
- 脂肪: 0.8-1.0 g/kg/天, 或总热量 20%-30%。
- 纤维: 25-35g/天; 饮水 30-40 ml/kg/天, 长跑出汗额外补水和电解质。
- 学生食堂预算方案: 两荤一素 + 米饭是基础; 高训练日加豆浆/酸奶/牛奶/香蕉; 避免长期只喝高糖饮料替代正餐。
- 输出顺序必须是: 1) 目标热量与三大营养素克数; 2) 餐次安排; 3) 具体食堂餐单; 4) 注意事项。
- 医疗边界: 不做疾病治疗承诺; 如有肾病、糖尿病、严重胃肠问题或进食障碍, 建议咨询医生/营养师。
""",
    "coach": """
【AI 私教知识库】
- 先看用户近期训练量、动作评分、动作错误类型, 再给建议。
- 动作质量优先于数量: 深蹲重点看膝髋同步、核心收紧、下蹲深度; 太浅提醒髋膝充分屈曲, 太深提醒膝盖压力与控制。
- 训练安排遵循: 热身 -> 主训练 -> 辅助训练 -> 拉伸; 强弱项交替; 疼痛优先降级或停止。
- 输出要具体到动作、组数、次数、间歇和技术提示。
""",
    "plan": """
【训练计划知识库】
- 计划按目标拆分: 增肌/减脂/耐力/体态/恢复。
- 渐进超负荷: 每周只增加一个变量, 比如 reps、sets、难度或总量, 不要全部同时增加。
- 高跑量用户要避免下肢力量和高强度跑步连续硬叠, 注意恢复日。
- 计划必须能被当前 App 的动作类型承载: squat, push_up, lunge, plank, bicep_curl, shoulder_press, jumping_jack 等。
""",
    "analysis": """
【运动数据分析知识库】
- 分析维度: 频率、总量、时长、平均评分、动作分布、错误类型、近期趋势、训练一致性。
- 结论要引用用户真实数据, 比如近 14/28 天训练次数、reps、评分、弱项动作。
- 优先指出一个最值得改的瓶颈, 再给 2-3 个下一步动作。
""",
}

INTENT_KEYWORDS = {
    "nutrition": ["饮食", "吃", "营养", "蛋白", "碳水", "热量", "餐", "食堂", "减脂餐", "增肌餐", "规划饮", "喝", "饭"],
    "plan": ["计划", "训练安排", "规划训练", "增肌", "减脂", "耐力", "周期", "周计划"],
    "analysis": ["分析", "数据", "复盘", "表现", "哪里", "问题", "趋势", "评分"],
    "coach": ["教练", "动作", "姿势", "深蹲", "太浅", "太深", "疼", "建议", "怎么练"],
}


def detect_domains(message: str) -> List[str]:
    msg = (message or "").lower()
    domains: List[str] = []
    for domain, kws in INTENT_KEYWORDS.items():
        if any(k.lower() in msg for k in kws):
            domains.append(domain)
    if not domains:
        domains = ["coach", "analysis"]
    if "nutrition" in domains:
        # 饮食问题通常也要看训练数据和身体数据
        for d in ("analysis", "plan"):
            if d not in domains:
                domains.append(d)
    return domains[:4]


def _context_snapshot(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_id": ctx.get("user_id"),
        "username": ctx.get("username"),
        "body": ctx.get("body"),
        "streak_days": ctx.get("streak_days"),
        "today_exercises": ctx.get("today_exercises", [])[:8],
        "weekly_summary": ctx.get("weekly_summary", [])[:14],
        "per_exercise": ctx.get("per_exercise", [])[:10],
        "plans": ctx.get("plans", [])[:3],
        "coach_memory": ctx.get("coach_memory", [])[:10],
    }


def _fallback_nutrition(ctx: Dict[str, Any]) -> str:
    body = ctx.get("body") or {}
    weight = float(body.get("weight_kg") or 55)
    protein_lo, protein_hi = round(weight * 1.6), round(weight * 2.0)
    carb_lo, carb_hi = round(weight * 4.0), round(weight * 6.0)
    fat_lo, fat_hi = round(weight * 0.8), round(weight * 1.0)
    return f"""## 营养目标粗估
- 蛋白质: {protein_lo}-{protein_hi}g/天
- 碳水: {carb_lo}-{carb_hi}g/天
- 脂肪: {fat_lo}-{fat_hi}g/天
- 饮水: 1.8-2.5L/天, 跑步出汗多时额外补水

## 食堂吃法
- 早餐: 鸡蛋/牛奶或豆浆 + 包子/馒头/米粉 + 一份水果
- 午餐: 两荤一素 + 米饭, 优先鸡肉/鱼/瘦肉/豆腐, 青菜别省
- 晚餐: 一荤一蛋白豆制品 + 一素 + 米饭; 长跑日可加香蕉/酸奶
- 加餐: 训练后 30-60 分钟内补牛奶/酸奶/豆浆 + 香蕉或面包

## 注意
你训练量偏高, 不建议为了减脂大幅砍碳水; 先保证蛋白和主食, 再控制高糖饮料频率。"""


def _extract_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    raw = text.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        raw = m.group(0)
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def respond(conn: sqlite3.Connection, user_id: int, message: str, mode: str = "auto", history=None) -> Dict[str, Any]:
    ctx = ai_planner._load_user_context(conn, user_id)
    ctx_block = ai_planner._build_user_context_block(ctx)
    domains = detect_domains(message if mode == "auto" else f"{mode} {message}")
    if mode in KNOWLEDGE_BASE and mode not in domains:
        domains.insert(0, mode)
    kb = "\n".join(KNOWLEDGE_BASE[d] for d in domains if d in KNOWLEDGE_BASE)
    nutrition_must = ""
    if "nutrition" in domains:
        nutrition_must = f"""
【本轮强制任务】
用户已经明确要求营养/饮食规划, 不要反问“你想问什么”, 不要泛泛聊天。
必须直接输出以下结构:
1. 用户情况分析: 引用体重、身高、BMI、今日/近期训练量。
2. 各类营养推荐量: 热量(kcal)、蛋白质(g)、碳水(g)、脂肪(g)、纤维(g)、饮水(L), 给范围并解释。
3. 具体饮食餐建议: 早餐/午餐/晚餐/训练后加餐, 尽量用学生食堂可买到的菜。
4. 与训练计划/训练数据匹配的调整: 高强度日、休息日分别怎么加减碳水和蛋白。
可参考的保底营养目标如下(若用户数据更完整可微调):
{_fallback_nutrition(ctx)}
"""
    system = f"""你是 Smart Fitness 的专属健身 Agent, 只服务当前登录用户。
你不是泛泛聊天助手, 你要根据用户身体数据、训练数据、计划、教练记忆和领域知识库给出个性化建议。
你拥有四个子专家: 训练计划师、运动数据分析师、AI 私教、AI 营养师。
当用户提饮食/营养, 必须先给各类营养目标量, 再给具体餐次建议。
当用户提训练, 必须结合近期训练数据和当前计划。
回答中文, 结构清楚, 具体可执行, 不要说“作为AI”。

{kb}
{nutrition_must}
{ctx_block}"""
    msgs = [{"role": "system", "content": system}]
    if history:
        for h in history[-8:]:
            msgs.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    msgs.append({"role": "user", "content": message})
    text = ai_planner._call_llm(
        msgs,
        max_tokens=1800,
        temperature=0.35 if "nutrition" in domains else 0.45,
        chain=os.environ.get("AI_AGENT_CHAT_CHAIN", "deepseek,qwen,volc-coding,hunyuan"),
    )
    if "nutrition" in domains and text:
        must_words = ["蛋白", "碳水", "脂肪", "早餐", "午餐", "晚餐"]
        if not all(w in text for w in must_words):
            text = _fallback_nutrition(ctx) + "\n\n## 进一步个性化\n" + text
    if not text and "nutrition" in domains:
        text = _fallback_nutrition(ctx)
    if not text:
        text = "我现在连不上大模型, 但可以先看你的训练数据: 近 28 天主要训练是 " + \
               ", ".join([f"{x.get('exercise')} {x.get('total_reps')}个" for x in ctx.get("per_exercise", [])[:3]]) + \
               "。你可以稍后再让我生成完整方案。"
    return {
        "ok": True,
        "mode": mode,
        "domains": domains,
        "reply": text,
        "context": _context_snapshot(ctx),
    }


def nutrition_plan(conn: sqlite3.Connection, user_id: int, goal: str = "维持训练表现并优化体成分") -> Dict[str, Any]:
    msg = f"请以 AI 营养师身份, 根据我的身体数据、训练数据和计划, 为我规划饮食。目标: {goal}。先给热量/蛋白质/碳水/脂肪/水/纤维目标量, 再给具体食堂三餐和加餐建议。"
    res = respond(conn, user_id, msg, mode="nutrition")
    parsed = _extract_json(res.get("reply") or "")
    if parsed:
        res["structured"] = parsed
    return res
