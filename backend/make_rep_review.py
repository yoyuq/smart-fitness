# -*- coding: utf-8 -*-
"""把已记录的训练 rep(合格/不合格)导出成自包含 HTML 回顾报告。
实拍关键帧已随清数据删除, 故用每个 rep 的关节角度轨迹 + 评分还原动作质量。"""
import sqlite3, json, base64, io, html, datetime
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

PASS = 85
EX = {"squat": "深蹲", "lunge": "弓步", "push_up": "俯卧撑", "jumping_jack": "开合跳",
      "plank": "平板支撑", "bicep_curl": "二头弯举", "shoulder_press": "肩推"}
OUT = r"C:\Users\hjl\Desktop\Smart_Fitness_训练回顾.html"


def b64(fig):
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=92, bbox_inches="tight"); plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def load(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute("SELECT * FROM rep_scores ORDER BY exercise, ts")]
    c.close(); return rows


def summary_fig(rows):
    exs = sorted(set(r["exercise"] for r in rows))
    pas = [sum(1 for r in rows if r["exercise"] == e and r["total"] >= PASS) for e in exs]
    fal = [sum(1 for r in rows if r["exercise"] == e and r["total"] < PASS) for e in exs]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.6))
    x = np.arange(len(exs))
    a1.bar(x - 0.2, pas, 0.4, label="合格", color="#22c55e")
    a1.bar(x + 0.2, fal, 0.4, label="不合格", color="#ef4444")
    a1.set_xticks(x); a1.set_xticklabels([EX.get(e, e) for e in exs])
    a1.legend(); a1.set_title("各动作 合格 / 不合格 数量"); a1.grid(axis="y", alpha=0.3)
    a2.hist([r["total"] for r in rows], bins=20, color="#3b82f6", alpha=0.85)
    a2.axvline(PASS, color="#ef4444", ls="--", lw=2, label=f"合格线 {PASS}")
    a2.set_title("总分分布"); a2.set_xlabel("总分"); a2.legend(); a2.grid(alpha=0.3)
    return b64(fig)


def contrast_fig(rows, ex):
    sub = [r for r in rows if r["exercise"] == ex and r["angle_series"]]
    if not sub:
        return None
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    npass = nfail = 0
    for r in sub:
        try:
            prim = json.loads(r["angle_series"]).get("primary")
        except Exception:
            continue
        if not prim:
            continue
        ok = r["total"] >= PASS
        ax.plot(prim, color="#22c55e" if ok else "#ef4444", alpha=0.4, lw=1.2)
        npass += ok; nfail += (not ok)
    ax.set_title(f"{EX.get(ex, ex)}  主关节角度轨迹\n绿={npass} 合格   红={nfail} 不合格")
    ax.set_xlabel("帧 (0–31 重采样)"); ax.set_ylabel("主关节角度 °"); ax.grid(alpha=0.3)
    return b64(fig)


def section(rows, title, subtitle):
    if not rows:
        return f"<section><h2>{html.escape(title)}</h2><p class=muted>无数据</p></section>"
    npass = sum(1 for r in rows if r["total"] >= PASS)
    nfail = len(rows) - npass
    avg = sum(r["total"] for r in rows) / len(rows)
    exs = sorted(set(r["exercise"] for r in rows))
    # 对比图(每动作一张)
    figs = ""
    for e in exs:
        src = contrast_fig(rows, e)
        if src:
            figs += f'<div class=card><img src="{src}"></div>'
    # 明细表
    trs = ""
    for i, r in enumerate(sorted(rows, key=lambda r: (r["exercise"], -r["total"])), 1):
        ok = r["total"] >= PASS
        badge = '<span class="b pass">合格</span>' if ok else '<span class="b fail">不合格</span>'
        fb = html.escape(r.get("feedback") or "")
        trs += (f'<tr class="{ "rp" if ok else "rf" }"><td>{i}</td><td>{EX.get(r["exercise"], r["exercise"])}</td>'
                f'<td>{badge}</td><td><b>{r["total"]:.0f}</b></td>'
                f'<td>{(r["depth"] or 0):.0f}</td><td>{(r["control"] or 0):.0f}</td><td>{(r["symmetry"] or 0):.0f}</td>'
                f'<td>{(r["peak_angle"] or 0):.0f}°</td><td>{(r["duration_s"] or 0):.1f}s</td><td class=fb>{fb}</td></tr>')
    return f"""<section>
      <h2>{html.escape(title)}</h2>
      <p class=muted>{html.escape(subtitle)}</p>
      <div class=stats>
        <div class=stat><div class=num>{len(rows)}</div><div>总次数</div></div>
        <div class=stat><div class="num g">{npass}</div><div>合格 (≥{PASS})</div></div>
        <div class=stat><div class="num r">{nfail}</div><div>不合格 (&lt;{PASS})</div></div>
        <div class=stat><div class=num>{avg:.1f}</div><div>平均分</div></div>
      </div>
      <img src="{summary_fig(rows)}" style="max-width:100%;margin:8px 0">
      <h3>各动作轨迹对比（绿=合格 / 红=不合格）</h3>
      <div class=grid>{figs}</div>
      <h3>逐次明细（共 {len(rows)} 次，按动作+分数排序）</h3>
      <table><thead><tr><th>#</th><th>动作</th><th>判定</th><th>总分</th><th>深度</th><th>控制</th><th>对称</th><th>峰值角</th><th>时长</th><th>教练反馈</th></tr></thead>
      <tbody>{trs}</tbody></table>
    </section>"""


def main():
    main_rows = load("fitness.db.bak_20260624_173717")
    cam_rows = load("fitness.db.bak_20260624_190707")
    body = section(main_rows, "全部已记录训练（232 次）",
                   "含你的摄像头训练 + 标定阶段用真实视频灌入管线产生的 rep；判定门槛 总分≥85=合格。")
    body += section(cam_rows, "最近一次 ESP32 摄像头深蹲采集（25 次）",
                    "你清数据后用 ESP32-CAM 真实采集的深蹲，全部为同一来源。")
    gen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    doc = f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
    <meta name=viewport content="width=device-width,initial-scale=1">
    <title>Smart Fitness 训练回顾</title><style>
    body{{font-family:'Microsoft YaHei',system-ui,sans-serif;margin:0;background:#0f172a;color:#e2e8f0;line-height:1.5}}
    .wrap{{max-width:1080px;margin:0 auto;padding:20px}}
    h1{{margin:.2em 0}} h2{{border-left:4px solid #3b82f6;padding-left:10px;margin-top:1.6em}}
    h3{{color:#93c5fd;margin-top:1.3em}} .muted{{color:#94a3b8;font-size:14px}}
    .stats{{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}}
    .stat{{background:#1e293b;border-radius:12px;padding:14px 22px;text-align:center;min-width:96px}}
    .num{{font-size:30px;font-weight:700}} .num.g{{color:#22c55e}} .num.r{{color:#ef4444}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}}
    .card{{background:#1e293b;border-radius:12px;padding:8px}} .card img{{width:100%;display:block;border-radius:6px}}
    table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}}
    th,td{{padding:6px 8px;text-align:center;border-bottom:1px solid #334155}}
    th{{background:#1e293b;position:sticky;top:0}} td.fb{{text-align:left;color:#cbd5e1;max-width:280px}}
    tr.rf{{background:rgba(239,68,68,.08)}} tr.rp{{background:rgba(34,197,94,.05)}}
    .b{{padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}}
    .b.pass{{background:#14532d;color:#86efac}} .b.fail{{background:#7f1d1d;color:#fca5a5}}
    header{{background:#1e293b;border-radius:14px;padding:18px 22px;margin-bottom:8px}}
    </style></head><body><div class=wrap>
    <header><h1>🏋️ Smart Fitness · 训练回顾</h1>
    <p class=muted>生成于 {gen} · 判定门槛 总分 ≥ {PASS} 为「合格」（与监控台/TCN评估口径一致）<br>
    ⚠️ 实拍画面已随之前清数据删除，本报告用每次动作的<b>关节角度轨迹 + 三项评分</b>还原质量供你审阅。</p></header>
    {body}
    <p class=muted style="margin-top:30px">深度/控制/对称为评分三分项（0–100）；峰值角=动作最深处主关节角度；轨迹横轴为重采样到 32 帧的归一化时间。</p>
    </div></body></html>"""
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(doc)
    print("OK ->", OUT)
    print(f"主数据 {len(main_rows)} 次 / 摄像头 {len(cam_rows)} 次")


if __name__ == "__main__":
    main()
