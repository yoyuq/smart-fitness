"""eval_tcn_passfail.py — TCN 判「合格/不合格」的诚实准确率评估

依据: 用【AI 评审分】当独立真值(不循环用规则分), 门槛 T 分以上=合格。
方法: TCN 二分类 + 重复分层交叉验证(数据少, 用 CV 而非单次划分)。
诚实呈现: 准确率 / 多数类基线 / 抓"不合格"的召回 / 混淆矩阵。
产物: datasets/models/tcn_eval.json (供监控台展示) + 一张评估图。
"""
import os, sys, json, sqlite3
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ml_pose"))
from poc_skeleton_model import build_tcn  # 复用同一 TCN 结构

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fitness.db")
OUT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "datasets", "models", "tcn_eval.json")
OUT_FIG = r"C:\Users\hjl\Desktop\TCN_合格判定_准确率评估.png"
CHANNELS = ["primary", "torso", "lr_diff", "shoulder"]
T_PASS = 85           # AI 分 >=85 视为"合格"(标准动作)
SERIES = 32


def load():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = c.execute("SELECT r.exercise ex, r.angle_series s, a.ai_score ai "
                     "FROM rep_scores r JOIN ai_reviews a ON a.rep_id=r.id "
                     "WHERE a.ai_score IS NOT NULL AND r.angle_series IS NOT NULL").fetchall()
    c.close()
    X, y, ex = [], [], []
    for r in rows:
        s = json.loads(r["s"])
        cols = []
        for ch in CHANNELS:
            v = s.get(ch) or [0.0] * SERIES
            v = [(x if x is not None else 0.0) for x in v]
            v = (v + [0.0] * SERIES)[:SERIES]
            cols.append(v)
        X.append(np.array(cols, np.float32))         # (4,32)
        y.append(1 if r["ai"] >= T_PASS else 0)      # 1=合格 0=不合格
        ex.append(r["ex"])
    return np.stack(X), np.array(y), ex


def train_predict(Xtr, ytr, Xte, seed):
    import torch, torch.nn as nn
    torch.manual_seed(seed); rng = np.random.default_rng(seed)
    mu = Xtr.mean((0, 2), keepdims=True); sd = Xtr.std((0, 2), keepdims=True) + 1e-6
    Xtr = (Xtr - mu) / sd; Xte = (Xte - mu) / sd
    # 增强(放大少数类)
    Xa, ya = [], []
    for i in range(len(Xtr)):
        reps = 14 if ytr[i] == 0 else 8           # 不合格样本少, 多增强
        Xa.append(Xtr[i]); ya.append(ytr[i])
        for _ in range(reps):
            xx = Xtr[i] + rng.normal(0, 0.07, Xtr[i].shape).astype(np.float32)
            xx = xx * np.float32(0.9 + 0.2 * rng.random())
            Xa.append(xx); ya.append(ytr[i])
    Xa = torch.tensor(np.stack(Xa)); ya = torch.tensor(np.array(ya))
    w = torch.tensor([1.0, 1.0]);
    model = build_tcn(4, 2)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    lossf = nn.CrossEntropyLoss()
    n = len(Xa); bs = 64; model.train()
    for ep in range(70):
        perm = torch.randperm(n)
        for i in range(0, n, bs):
            b = perm[i:i + bs]
            opt.zero_grad(); loss = lossf(model(Xa[b]), ya[b]); loss.backward(); opt.step()
    model.eval()
    import torch as T
    with T.no_grad():
        return model(T.tensor(Xte)).argmax(1).numpy()


def main():
    X, y, ex = load()
    n = len(y); nfail = int((y == 0).sum())
    print(f"样本 {n} (合格 {n-nfail} / 不合格 {nfail}, 门槛 AI>={T_PASS})")
    from sklearn.model_selection import RepeatedStratifiedKFold
    rskf = RepeatedStratifiedKFold(n_splits=5, n_repeats=6, random_state=0)
    yt_all, yp_all = [], []
    for k, (tr, te) in enumerate(rskf.split(X, y)):
        yp = train_predict(X[tr], y[tr], X[te], seed=k)
        yt_all.extend(y[te].tolist()); yp_all.extend(yp.tolist())
    yt = np.array(yt_all); yp = np.array(yp_all)

    acc = float((yt == yp).mean())
    # 多数类基线(全判多数类)
    base = float(max((y == 1).mean(), (y == 0).mean()))
    # 各类召回
    rec_fail = float(((yp == 0) & (yt == 0)).sum() / max((yt == 0).sum(), 1))
    rec_pass = float(((yp == 1) & (yt == 1)).sum() / max((yt == 1).sum(), 1))
    bal = (rec_fail + rec_pass) / 2
    # 混淆矩阵 [真实][预测]
    cm = [[int(((yt == a) & (yp == b)).sum()) for b in (0, 1)] for a in (0, 1)]

    res = {"n": n, "n_fail": nfail, "threshold": T_PASS,
           "accuracy": round(acc, 3), "baseline_majority": round(base, 3),
           "balanced_acc": round(bal, 3), "recall_fail": round(rec_fail, 3),
           "recall_pass": round(rec_pass, 3), "confusion": cm,
           "cv": "RepeatedStratifiedKFold 5x6", "label_source": "AI评审分",
           "note": "数据少且偏(多数合格), 准确率须对比多数类基线; 抓不合格的召回更关键"}
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    json.dump(res, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))

    # ---- 图 ----
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]; plt.rcParams["axes.unicode_minus"] = False
        BG = "#0E141B"; PN = "#18222E"; GR = "#2DE39E"; RD = "#FF6B6B"; SB = "#9AA8B6"; TX = "#F2F6FA"
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 5), facecolor=BG)
        # 左: 指标对比
        a1.set_facecolor(PN)
        labels = ["TCN 准确率", "多数类基线\n(全判合格)", "抓'不合格'\n召回率"]
        vals = [acc * 100, base * 100, rec_fail * 100]; cols = [GR, SB, RD]
        a1.bar(labels, vals, color=cols)
        for i, v in enumerate(vals): a1.text(i, v + 2, "%.0f%%" % v, color=cols[i], ha="center", fontweight="bold")
        a1.set_ylim(0, 110); a1.set_title("TCN 判合格/不合格 · 诚实指标", color=TX, fontweight="bold")
        a1.tick_params(colors=SB); [s.set_color("#2A3744") for s in a1.spines.values()]
        # 右: 混淆矩阵
        a2.set_facecolor(PN)
        cmn = np.array(cm)
        a2.imshow(cmn, cmap="Greens")
        for i in range(2):
            for j in range(2):
                a2.text(j, i, cmn[i, j], ha="center", va="center", color="black" if cmn[i, j] > cmn.max() / 2 else TX, fontsize=16, fontweight="bold")
        a2.set_xticks([0, 1]); a2.set_xticklabels(["判不合格", "判合格"], color=SB)
        a2.set_yticks([0, 1]); a2.set_yticklabels(["真·不合格", "真·合格"], color=SB)
        a2.set_title("混淆矩阵", color=TX, fontweight="bold")
        fig.suptitle("TCN 模型「合格判定」准确率评估  (依据: %d 条真实 rep · AI评审标签 · 5×6 折交叉验证)" % n,
                     color=TX, fontsize=13, fontweight="bold")
        fig.text(0.5, 0.01, "结论: 准确率 %.0f%% vs 多数类基线 %.0f%%;数据少且偏,抓'不合格'召回 %.0f%% —— 需更多不合格样本才可靠"
                 % (acc * 100, base * 100, rec_fail * 100), color=SB, ha="center", fontsize=9)
        plt.tight_layout(rect=[0, 0.03, 1, 0.95]); plt.savefig(OUT_FIG, dpi=130, facecolor=BG); plt.close()
        print("SAVED", OUT_FIG)
    except Exception as e:
        print("fig skip:", e)


if __name__ == "__main__":
    main()
