"""poc_widen_input.py — A: 加宽模型输入通道, 看能否更贴 AI 评审分

对照实验(同一批 rep、同一目标=AI分、同一划分、3 seed):
  4 通道(旧, rep_scores.angle_series): primary/torso/lr_diff/shoulder
  13 通道(新, 从 pose_data.angles_json 重建): 双侧 knee/hip/elbow/shoulder + torso
                                              + ankle_dx/wrist_above/head_drop/head_fwd
前者缺"膝内扣/头前探"等 AI 真正在看的量, 后者补上。看 holdout MAE 是否下降。
"""
import os, sys, json, sqlite3
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from poc_skeleton_model import build_tcn

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "fitness.db")
T = 32
C4 = ["primary", "torso", "lr_diff", "shoulder"]
C13 = ["knee_L", "knee_R", "hip_L", "hip_R", "elbow_L", "elbow_R",
       "shoulder_L", "shoulder_R", "torso_tilt", "ankle_dx",
       "wrist_above", "head_drop", "head_fwd"]


def resample(vals):
    a = np.array([np.nan if v is None else float(v) for v in vals], np.float32)
    if len(a) == 0 or np.isnan(a).all():
        return np.zeros(T, np.float32)
    if np.isnan(a).any():
        idx = np.arange(len(a)); good = ~np.isnan(a)
        a = np.interp(idx, idx[good], a[good]).astype(np.float32)
    return np.interp(np.linspace(0, len(a) - 1, T), np.arange(len(a)), a).astype(np.float32)


def series4_from_stored(s):
    return np.stack([resample(s.get(ch) or []) for ch in C4])     # (4,T)


def build_dataset():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    reps = c.execute(
        "SELECT r.id, r.session_id sid, r.rep_index ri, r.exercise ex, r.angle_series s4, a.ai_score ai "
        "FROM rep_scores r JOIN ai_reviews a ON a.rep_id=r.id "
        "WHERE a.ai_score IS NOT NULL AND r.angle_series IS NOT NULL").fetchall()
    # 预取每个 session 的帧, 按 rep_count 分组
    sess_groups = {}
    for rep in reps:
        sid = rep["sid"]
        if sid in sess_groups:
            continue
        rows = c.execute("SELECT timestamp ts, rep_count rc, angles_json aj FROM pose_data "
                         "WHERE session_id=? AND angles_json IS NOT NULL ORDER BY timestamp", (sid,)).fetchall()
        g = {}
        for x in rows:
            g.setdefault(x["rc"], []).append(x["aj"])
        sess_groups[sid] = g
    c.close()

    X4, X13, Y = [], [], []
    matched = 0
    for rep in reps:
        try:
            s4 = json.loads(rep["s4"])
        except Exception:
            continue
        groups = sess_groups.get(rep["sid"], {})
        frames = None
        for rc in (rep["ri"] - 1, rep["ri"]):           # rep_count 通常= rep_index-1
            if rc in groups and len(groups[rc]) >= 6:
                frames = groups[rc]; break
        if frames is None:
            continue
        # 13 通道
        per = {ch: [] for ch in C13}
        for aj in frames:
            try:
                d = json.loads(aj)
            except Exception:
                d = {}
            for ch in C13:
                per[ch].append(d.get(ch))
        m13 = np.stack([resample(per[ch]) for ch in C13])         # (13,T)
        X13.append(m13); X4.append(series4_from_stored(s4)); Y.append(float(rep["ai"]))
        matched += 1
    print(f"AI 标注 rep: {len(reps)}  成功重建 13 通道: {matched}")
    return np.stack(X4), np.stack(X13), np.array(Y, np.float32)


def tcn_mae(X, y, seeds=(0, 1, 2)):
    import torch, torch.nn as nn
    maes = []
    for s in seeds:
        torch.manual_seed(s); rng = np.random.default_rng(s)
        idx = rng.permutation(len(X)); ntr = int(len(X) * 0.8)
        tr, te = idx[:ntr], idx[ntr:]
        mu = X[tr].mean((0, 2), keepdims=True); sd = X[tr].std((0, 2), keepdims=True) + 1e-6
        Xtr = ((X[tr] - mu) / sd).astype(np.float32); Xte = ((X[te] - mu) / sd).astype(np.float32)
        # 增强
        Xa, ya = [], []
        for i in range(len(Xtr)):
            Xa.append(Xtr[i]); ya.append(y[tr][i])
            for _ in range(10):
                xx = Xtr[i] + rng.normal(0, 0.06, Xtr[i].shape).astype(np.float32)
                xx = xx * np.float32(0.9 + 0.2 * rng.random())
                Xa.append(xx); ya.append(y[tr][i])
        Xa = torch.tensor(np.stack(Xa)); ya = torch.tensor(np.array(ya, np.float32) / 100).view(-1, 1)
        model = build_tcn(X.shape[1], 1)
        opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
        lossf = nn.MSELoss(); n = len(Xa); bs = 64
        model.train()
        for ep in range(120):
            perm = torch.randperm(n)
            for i in range(0, n, bs):
                b = perm[i:i + bs]
                opt.zero_grad(); loss = lossf(model(Xa[b]), ya[b]); loss.backward(); opt.step()
        model.eval()
        with torch.no_grad():
            pred = model(torch.tensor(Xte)).numpy().ravel() * 100
        maes.append(float(np.mean(np.abs(pred - y[te]))))
    return float(np.mean(maes)), float(np.std(maes))


def main():
    X4, X13, Y = build_dataset()
    if len(Y) < 12:
        print("可用样本太少, 无法对比"); return
    print(f"\n同一批 {len(Y)} 个 rep, 目标=AI分, 3 seed:")
    m4, s4 = tcn_mae(X4, Y)
    m13, s13 = tcn_mae(X13, Y)
    print(f"  4 通道 (旧)  holdout MAE = {m4:.1f} ± {s4:.1f}")
    print(f"  13 通道(新)  holdout MAE = {m13:.1f} ± {s13:.1f}")
    delta = m4 - m13
    print(f"\n  加宽输入 {'有效 ↓ MAE 降 %.1f 分' % delta if delta > 0.5 else '未见明显改善'}"
          f"  (越低越好)")
    json.dump({"n": len(Y), "mae_4ch": m4, "mae_13ch": m13},
              open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "poc_widen_results.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
