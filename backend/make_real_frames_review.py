# -*- coding: utf-8 -*-
"""用真实关键帧(start/peak/end)+ 评分生成动作实拍回顾。
图片在团队副本 smart_fitness_team/backend/data/rep_frames/, 评分取自备份库 bak_173717。
报告写到团队副本 backend/ 下, 用相对路径引用图片(保证能加载)。"""
import sqlite3, os, html, datetime

TEAM = r"C:\Users\hjl\Desktop\smart_fitness_team\backend"
DB = r"C:\Users\hjl\.openclaw\workspace\smart_fitness\backend\fitness.db.bak_20260624_173717"
OUT = os.path.join(TEAM, "动作实拍回顾.html")
PASS = 85
EX = {"squat": "深蹲", "lunge": "弓步", "push_up": "俯卧撑", "jumping_jack": "开合跳",
      "plank": "平板支撑", "bicep_curl": "二头弯举", "shoulder_press": "肩推"}


def rel(p):
    """DB 里存的是 'data\\rep_frames\\...' (相对 backend), 转成 HTML 用的相对 URL。"""
    return p.replace("\\", "/")


def main():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM rep_scores WHERE start_frame IS NOT NULL AND start_frame<>'' ORDER BY exercise, ts")]
    c.close()

    # 只保留三帧实拍齐全的
    reps = []
    for r in rows:
        paths = [r["start_frame"], r["peak_frame"], r["end_frame"]]
        if all(p and os.path.exists(os.path.join(TEAM, p.replace("\\", os.sep))) for p in paths):
            reps.append(r)

    npass = sum(1 for r in reps if r["total"] >= PASS)
    nfail = len(reps) - npass
    exs = sorted(set(r["exercise"] for r in reps), key=lambda e: -sum(1 for r in reps if r["exercise"] == e))

    def card(r):
        ok = r["total"] >= PASS
        sf, pf, ef = rel(r["start_frame"]), rel(r["peak_frame"]), rel(r["end_frame"])
        fb = html.escape(r.get("feedback") or "—")
        badge = '<span class="b pass">合格</span>' if ok else '<span class="b fail">不合格</span>'
        return f"""<div class="card {'p' if ok else 'f'}" data-pass="{1 if ok else 0}">
          <div class=head>{EX.get(r['exercise'], r['exercise'])} · 第{r['rep_index']}次 {badge}
            <span class=score style="color:{'#22c55e' if ok else '#ef4444'}">{r['total']:.0f}分</span></div>
          <div class=strip>
            <figure><img loading=lazy src="{sf}"><figcaption>开始</figcaption></figure>
            <figure><img loading=lazy src="{pf}"><figcaption>最深/峰值</figcaption></figure>
            <figure><img loading=lazy src="{ef}"><figcaption>结束</figcaption></figure>
          </div>
          <div class=meta>深度 {(r['depth'] or 0):.0f} · 控制 {(r['control'] or 0):.0f} · 对称 {(r['symmetry'] or 0):.0f}
            · 峰值角 {(r['peak_angle'] or 0):.0f}° · {(r['duration_s'] or 0):.1f}s</div>
          <div class=fb>{fb}</div>
        </div>"""

    sections = ""
    for e in exs:
        sub = [r for r in reps if r["exercise"] == e]
        fails = [r for r in sub if r["total"] < PASS]
        passes = [r for r in sub if r["total"] >= PASS]
        ep = sum(1 for r in sub if r["total"] >= PASS)
        cards = "".join(card(r) for r in fails + passes)  # 不合格在前, 便于审阅
        sections += f"""<section id="ex-{e}"><h2>{EX.get(e, e)} <span class=muted>（{len(sub)} 次 · 合格 {ep} / 不合格 {len(sub)-ep}）</span></h2>
          <div class=grid>{cards}</div></section>"""

    nav = " · ".join(f'<a href="#ex-{e}">{EX.get(e,e)}({sum(1 for r in reps if r["exercise"]==e)})</a>' for e in exs)
    gen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    doc = f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
    <meta name=viewport content="width=device-width,initial-scale=1"><title>动作实拍回顾</title><style>
    body{{font-family:'Microsoft YaHei',system-ui,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}}
    .wrap{{max-width:1180px;margin:0 auto;padding:18px}}
    header{{background:#1e293b;border-radius:14px;padding:18px 22px}}
    h1{{margin:.2em 0}} h2{{border-left:4px solid #3b82f6;padding-left:10px;margin-top:1.4em}}
    .muted{{color:#94a3b8;font-size:14px;font-weight:400}}
    .bar{{position:sticky;top:0;z-index:5;background:#0f172aee;backdrop-filter:blur(6px);padding:10px 0;border-bottom:1px solid #334155;margin-bottom:8px}}
    .bar a{{color:#93c5fd;text-decoration:none;font-size:13px}} .bar a:hover{{text-decoration:underline}}
    .stats{{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}}
    .stat{{background:#1e293b;border-radius:12px;padding:12px 20px;text-align:center;min-width:90px}}
    .num{{font-size:26px;font-weight:700}} .g{{color:#22c55e}} .r{{color:#ef4444}}
    .filt button{{background:#1e293b;color:#e2e8f0;border:1px solid #475569;border-radius:20px;padding:6px 16px;margin-right:8px;cursor:pointer;font-size:13px}}
    .filt button.on{{background:#3b82f6;border-color:#3b82f6}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}}
    .card{{background:#1e293b;border-radius:12px;padding:10px;border-left:4px solid #475569}}
    .card.p{{border-left-color:#22c55e}} .card.f{{border-left-color:#ef4444}}
    .head{{font-weight:600;margin-bottom:6px}} .score{{float:right;font-weight:700}}
    .strip{{display:flex;gap:4px}} .strip figure{{margin:0;flex:1;text-align:center}}
    .strip img{{width:100%;border-radius:6px;background:#000;aspect-ratio:4/3;object-fit:cover}}
    .strip figcaption{{font-size:11px;color:#94a3b8;margin-top:2px}}
    .meta{{font-size:12px;color:#cbd5e1;margin-top:6px}} .fb{{font-size:12px;color:#fca5a5;margin-top:4px}}
    .card.p .fb{{color:#86efac}}
    .b{{padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}}
    .b.pass{{background:#14532d;color:#86efac}} .b.fail{{background:#7f1d1d;color:#fca5a5}}
    </style></head><body><div class=wrap>
    <header><h1>🎬 Smart Fitness · 动作实拍回顾</h1>
    <p class=muted>真实 ESP32-CAM 关键帧（开始 / 最深 / 结束）+ 评分。生成于 {gen} · 合格门槛 总分≥{PASS}<br>
    图片来自团队副本（清数据前留存），共 {len(reps)} 次动作可看实拍。</p>
    <div class=stats>
      <div class=stat><div class=num>{len(reps)}</div><div>可看实拍</div></div>
      <div class=stat><div class="num g">{npass}</div><div>合格</div></div>
      <div class=stat><div class="num r">{nfail}</div><div>不合格</div></div>
    </div>
    <div class=filt><button class=on onclick="flt(this,-1)">全部</button>
      <button onclick="flt(this,0)">只看不合格</button>
      <button onclick="flt(this,1)">只看合格</button></div>
    </header>
    <div class=bar>{nav}</div>
    {sections}
    </div>
    <script>
    function flt(btn,v){{document.querySelectorAll('.filt button').forEach(b=>b.classList.remove('on'));btn.classList.add('on');
      document.querySelectorAll('.card').forEach(c=>{{c.style.display=(v<0||+c.dataset.pass===v)?'':'none';}});}}
    </script></body></html>"""
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(doc)
    print("OK ->", OUT)
    print(f"可看实拍 {len(reps)} 次 (合格 {npass} / 不合格 {nfail})；动作: {[(EX.get(e,e), sum(1 for r in reps if r['exercise']==e)) for e in exs]}")


if __name__ == "__main__":
    main()
