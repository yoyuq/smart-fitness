"""ai_review.py - AI 评审团标定管线 (评分V2 第三阶段)

设计原则 (规避人工教练打分的主观偏差, 参考 FLEX 的结构化方法):
  - 评审模型不打 0-100 总分, 只对"错误清单"逐项勾选 是/否
  - 分数由确定性的组合函数从勾选结果算出 (可解释、可复现)
  - 支持多模型评审同一 rep, 取保守聚合; 全部结果入库可追溯

用法:
  python ai_review.py run [--limit 20]   # 批审还没评过的 rep (需有 peak_frame)
  python ai_review.py report             # 规则分 vs AI 评审分 的标定报告
"""
import argparse
import base64
import json
import os
import re
import sqlite3
import sys
import time

import requests

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "fitness.db")

# 评审模型: 百炼 qwen3-vl-plus (主) — key 从环境/.env 读
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND_DIR, ".env"))
except ImportError:
    pass
VL_KEY = os.environ.get("BAILIAN_API_KEY", "") or os.environ.get("DASHSCOPE_API_KEY", "")
VL_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
VL_MODEL = os.environ.get("AI_REVIEW_VL_MODEL", "qwen3-vl-plus")

# ============ 错误清单 (全部表述为"错误", 勾 true = 存在该问题) ============
# (key, 中文判定问题, 扣分权重)
CHECKLISTS = {
    "squat": [
        ("not_deep", "最深处髋关节没有降到接近或低于膝盖高度(深度不足)", 30),
        ("knee_valgus", "膝盖内扣(向身体中线塌陷)", 20),
        ("torso_lean", "躯干过度前倾(与垂直方向夹角明显大于45度)", 15),
        ("heels_up", "脚跟离地", 15),
        ("back_round", "下背弓起, 脊柱未保持中立", 20),
    ],
    "push_up": [
        ("not_low", "最低点胸部离地面还很远(下放不足)", 30),
        ("hip_sag", "塌腰(髋部下垂, 身体不成直线)", 25),
        ("hip_pike", "撅臀(髋部抬高)", 15),
        ("elbow_flare", "肘部过度外展(与躯干夹角接近90度)", 15),
        ("head_drop", "头部下垂或前探", 15),
    ],
    "lunge": [
        ("shallow", "前腿弯曲不足, 没有明显下沉", 30),
        ("knee_over", "前膝大幅超过脚尖", 20),
        ("torso_lean", "躯干明显前倾或侧倾", 20),
        ("knee_valgus", "前膝内扣", 20),
        ("stance_short", "步幅过小, 前后脚距离不足", 10),
    ],
    "bicep_curl": [
        ("partial_rom", "弯举幅度不足(没有充分收缩到顶)", 30),
        ("swing", "身体摆动借力", 25),
        ("elbow_drift", "肘部前移或抬起(肩部代偿)", 25),
        ("wrist_bend", "手腕过度弯曲", 10),
    ],
    "shoulder_press": [
        ("not_lockout", "顶部手臂没有接近伸直(未推到位)", 30),
        ("back_arch", "腰椎过度反弓", 25),
        ("uneven", "两侧高度明显不一致", 20),
        ("head_forward", "头部过度前探", 10),
    ],
    "jumping_jack": [
        ("arms_low", "手臂没有举过头顶", 30),
        ("feet_narrow", "双脚打开幅度不足", 20),
        ("not_synced", "手脚不同步", 20),
    ],
}


def _b64_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def review_rep(exercise, frame_paths, model=VL_MODEL, timeout=120):
    """对一次动作的关键帧做错误清单勾选. 返回 (errors_dict, person_visible, notes) 或 None."""
    checklist = CHECKLISTS.get(exercise)
    if not checklist or not VL_KEY:
        return None
    items = "\n".join(f'  "{k}": <true|false>  // {q}' for k, q, _ in checklist)
    prompt = f"""你是动作质量评审员。图片是一次 {exercise} 动作的关键帧(按顺序: 起始/最深点/结束, 可能不全)。
逐项判断下列错误是否存在。只依据画面证据, 看不清或无法判断的项填 false。
只输出 JSON, 不要其他文字:
{{
"person_visible": <true|false>,  // 画面中是否能看到做动作的完整人体
"errors": {{
{items}
}},
"notes": "<一句话补充, 20字内>"
}}"""
    content = [{"type": "text", "text": prompt}]
    for p in frame_paths:
        if p and os.path.exists(p):
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{_b64_image(p)}"}})
    if len(content) == 1:
        return None
    r = requests.post(VL_URL, headers={"Authorization": f"Bearer {VL_KEY}"},
                      json={"model": model, "max_tokens": 2000, "temperature": 0.1,
                            "messages": [{"role": "user", "content": content}]},
                      timeout=timeout)
    if r.status_code != 200:
        print(f"  [warn] VL HTTP {r.status_code}: {r.text[:120]}")
        return None
    raw = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    return (data.get("errors") or {}, bool(data.get("person_visible", True)),
            str(data.get("notes") or "")[:60])


def errors_to_score(exercise, errors):
    """确定性组合函数: 100 - Σ(存在错误的权重)."""
    score = 100
    for key, _q, weight in CHECKLISTS.get(exercise, []):
        if errors.get(key) is True:
            score -= weight
    return max(0, score)


def ensure_review_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rep_id INTEGER NOT NULL,
            model TEXT,
            person_visible INTEGER,
            errors_json TEXT,
            ai_score REAL,
            notes TEXT,
            created_at INTEGER
        )""")
    conn.commit()


def run_batch(limit=20):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_review_table(conn)
    rows = conn.execute(
        "SELECT r.* FROM rep_scores r "
        "LEFT JOIN ai_reviews a ON a.rep_id = r.id AND a.model = ? "
        "WHERE a.id IS NULL AND r.peak_frame IS NOT NULL "
        "ORDER BY r.id DESC LIMIT ?", (VL_MODEL, limit)).fetchall()
    print(f"待评审 rep: {len(rows)} (model={VL_MODEL})")
    done = 0
    for row in rows:
        frames = [os.path.join(BACKEND_DIR, p) for p in
                  (row["start_frame"], row["peak_frame"], row["end_frame"]) if p]
        t0 = time.time()
        res = review_rep(row["exercise"], frames)
        if res is None:
            print(f"  rep#{row['id']} {row['exercise']}: 评审失败/跳过")
            continue
        errors, visible, notes = res
        score = errors_to_score(row["exercise"], errors) if visible else None
        conn.execute(
            "INSERT INTO ai_reviews (rep_id, model, person_visible, errors_json, ai_score, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (row["id"], VL_MODEL, int(visible), json.dumps(errors, ensure_ascii=False),
             score, notes, int(time.time())))
        conn.commit()
        hit = [k for k, v in errors.items() if v]
        print(f"  rep#{row['id']} {row['exercise']}: 规则={row['total']:.0f} AI={score} "
              f"错误={hit or '无'} ({time.time()-t0:.1f}s) {notes}")
        done += 1
    conn.close()
    print(f"完成 {done}/{len(rows)}")


def report():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_review_table(conn)
    rows = conn.execute(
        "SELECT r.id, r.exercise, r.total AS rule_score, r.depth, r.control, r.symmetry, "
        "       a.ai_score, a.errors_json, a.notes "
        "FROM rep_scores r JOIN ai_reviews a ON a.rep_id = r.id "
        "WHERE a.ai_score IS NOT NULL").fetchall()
    if not rows:
        print("还没有可对比的评审数据")
        return
    diffs = [abs(r["rule_score"] - r["ai_score"]) for r in rows]
    print(f"=== 标定报告: {len(rows)} 个已评审 rep ===")
    print(f"规则分 vs AI 分 平均绝对差: {sum(diffs)/len(diffs):.1f}")
    by_ex = {}
    for r in rows:
        by_ex.setdefault(r["exercise"], []).append(r)
    for ex, items in by_ex.items():
        bias = sum(i["rule_score"] - i["ai_score"] for i in items) / len(items)
        print(f"  {ex}: n={len(items)}, 规则偏高 {bias:+.1f} 分")
    print("--- 分歧最大 top5 (标定线索) ---")
    for r in sorted(rows, key=lambda x: -abs(x["rule_score"] - x["ai_score"]))[:5]:
        print(f"  rep#{r['id']} {r['exercise']}: 规则={r['rule_score']:.0f} AI={r['ai_score']:.0f} "
              f"AI勾选={[k for k, v in json.loads(r['errors_json']).items() if v]} {r['notes']}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    if args.cmd == "run":
        run_batch(args.limit)
    else:
        report()
