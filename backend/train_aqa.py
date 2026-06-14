"""train_aqa.py - 时序动作质量评估(AQA)模型 (评分V2 第四阶段进阶)

把 AI 评审团标签(ai_reviews.errors_json)当训练目标, 把每个 rep 的**角度时序**
(rep_scores.angle_series, 4通道×32帧)当输入, 训练"每类错误一个时序分类器"。

模型: 轻量 GRU + 多通道时序输入 (torch). 数据稀少时用时序数据增强
(时间扭曲/幅度抖动/通道噪声)放大样本; 交叉验证给诚实指标, 不达标只报告。

用法:
  python train_aqa.py status   # 看可用时序+标注数据量(按动作×错误)
  python train_aqa.py train    # 训练并存 datasets/models/aqa_<ex>_<err>.pt
"""
import argparse
import json
import os
import sqlite3

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "fitness.db")
MODEL_DIR = os.path.join(BACKEND_DIR, "..", "datasets", "models")
SERIES_LEN = 32
CHANNELS = ["primary", "torso", "lr_diff", "shoulder"]
MIN_POS = 5        # 该错误至少多少正样本(增强前)才训练
MIN_NEG = 5
AUG_FACTOR = 12    # 每个真实样本增强出多少条

from ai_review import CHECKLISTS  # noqa: E402


def _load_series_labeled(conn):
    """取同时有角度时序 + 评审标签的 rep. 返回 [(ex, series_dict, errors), ...]."""
    rows = conn.execute(
        "SELECT r.exercise, r.angle_series, a.errors_json "
        "FROM rep_scores r JOIN ai_reviews a ON a.rep_id = r.id "
        "WHERE a.ai_score IS NOT NULL AND r.angle_series IS NOT NULL").fetchall()
    out = []
    for ex, sj, ej in rows:
        try:
            series = json.loads(sj); errors = json.loads(ej)
        except Exception:
            continue
        if not series or "primary" not in series:
            continue
        out.append((ex, series, errors))
    return out


def _to_matrix(series):
    """series_dict -> (SERIES_LEN, n_channels) float list."""
    cols = []
    for ch in CHANNELS:
        v = series.get(ch) or [0.0] * SERIES_LEN
        if len(v) != SERIES_LEN:
            v = (v + [0.0] * SERIES_LEN)[:SERIES_LEN]
        cols.append(v)
    # 转置: 行=时间, 列=通道
    return [[cols[c][t] for c in range(len(CHANNELS))] for t in range(SERIES_LEN)]


def _augment(mat, rng):
    """时序增强: 幅度缩放 + 通道高斯噪声 + 轻微时间平移. 返回新矩阵."""
    import numpy as np
    a = np.array(mat, dtype="float32")
    a = a * rng.uniform(0.95, 1.05)                       # 幅度缩放
    a = a + rng.normal(0, 1.5, a.shape).astype("float32") # 角度噪声(度)
    shift = int(rng.integers(-2, 3))                       # 时间平移 ±2 帧
    if shift:
        a = np.roll(a, shift, axis=0)
    return a.tolist()


def status():
    conn = sqlite3.connect(DB_PATH)
    data = _load_series_labeled(conn)
    conn.close()
    print(f"有角度时序+标注的 rep: {len(data)}")
    by_ex = {}
    for ex, _s, errors in data:
        d = by_ex.setdefault(ex, {"n": 0, "errors": {}})
        d["n"] += 1
        for k, v in errors.items():
            if v:
                d["errors"][k] = d["errors"].get(k, 0) + 1
    for ex, d in sorted(by_ex.items()):
        print(f"\n== {ex}: {d['n']} 个 ==")
        for k, _q, _w in CHECKLISTS.get(ex, []):
            pos = d["errors"].get(k, 0); neg = d["n"] - pos
            ok = "✓可训练" if (pos >= MIN_POS and neg >= MIN_NEG) else "数据不足"
            print(f"  {k}: 正{pos}/负{neg}  [{ok}]")
    print(f"\n(每错误需正/负各 >= {MIN_POS}/{MIN_NEG}; 训练时按 {AUG_FACTOR}x 增强)")


def train():
    try:
        import numpy as np
        import torch
        import torch.nn as nn
    except ImportError as e:
        print(f"缺依赖: {e}")
        return
    conn = sqlite3.connect(DB_PATH)
    data = _load_series_labeled(conn)
    conn.close()
    os.makedirs(MODEL_DIR, exist_ok=True)
    rng = np.random.default_rng(42)
    torch.manual_seed(42)

    class GRUClf(nn.Module):
        def __init__(self, n_ch, hidden=24):
            super().__init__()
            self.gru = nn.GRU(n_ch, hidden, batch_first=True)
            self.head = nn.Sequential(nn.Linear(hidden, 16), nn.ReLU(), nn.Linear(16, 1))

        def forward(self, x):
            _, h = self.gru(x)
            return self.head(h[-1]).squeeze(-1)

    trained = 0
    for ex in sorted(set(d[0] for d in data)):
        reps = [(s, e) for x, s, e in data if x == ex]
        for err_key, _q, _w in CHECKLISTS.get(ex, []):
            y = [1 if e.get(err_key) else 0 for _s, e in reps]
            pos, neg = sum(y), len(y) - sum(y)
            if pos < MIN_POS or neg < MIN_NEG:
                continue
            # 先按"真实 rep"分层划分 train/val (防泄露): 验证集只放未见过的真实 rep,
            # 只对训练集做增强. 否则同一 rep 的增强副本会同时进两边, acc 虚高.
            base = [(_to_matrix(s), lab) for (s, _e), lab in zip(reps, y)]
            pos_i = [i for i, (_m, l) in enumerate(base) if l == 1]
            neg_i = [i for i, (_m, l) in enumerate(base) if l == 0]
            rng.shuffle(pos_i); rng.shuffle(neg_i)
            n_val_pos = max(1, len(pos_i) // 5); n_val_neg = max(1, len(neg_i) // 5)
            val_idx = set(pos_i[:n_val_pos] + neg_i[:n_val_neg])
            Xtr_l, Ytr_l, Xva_l, Yva_l = [], [], [], []
            for i, (m, lab) in enumerate(base):
                if i in val_idx:
                    Xva_l.append(m); Yva_l.append(lab)               # 验证: 仅真实样本
                else:
                    Xtr_l.append(m); Ytr_l.append(lab)
                    for _ in range(AUG_FACTOR):                       # 增强: 仅训练集
                        Xtr_l.append(_augment(m, rng)); Ytr_l.append(lab)
            Xtr = np.array(Xtr_l, dtype="float32"); Ytr = np.array(Ytr_l, dtype="float32")
            Xva = np.array(Xva_l, dtype="float32"); Yva = np.array(Yva_l, dtype="float32")
            mu = Xtr.reshape(-1, len(CHANNELS)).mean(0); sd = Xtr.reshape(-1, len(CHANNELS)).std(0) + 1e-6
            Xt = torch.tensor((Xtr - mu) / sd); Yt = torch.tensor(Ytr)
            Xv = torch.tensor((Xva - mu) / sd); Yv = torch.tensor(Yva)
            model = GRUClf(len(CHANNELS))
            opt = torch.optim.Adam(model.parameters(), lr=0.01)
            lossf = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([neg / max(pos, 1)]))
            for epoch in range(60):
                model.train(); opt.zero_grad()
                loss = lossf(model(Xt), Yt); loss.backward(); opt.step()
            model.eval()
            with torch.no_grad():
                pv = (torch.sigmoid(model(Xv)) > 0.5).float()
                acc = (pv == Yv).float().mean().item() if len(Yv) else float("nan")
            path = os.path.join(MODEL_DIR, f"aqa_{ex}_{err_key}.pt")
            torch.save({"state": model.state_dict(), "channels": CHANNELS,
                        "series_len": SERIES_LEN, "mu": mu.tolist(), "sd": sd.tolist()}, path)
            print(f"  {ex}/{err_key}: 真实正{pos}/负{neg} → 训练{len(Xtr)}条(含增强), "
                  f"验证{len(Yva)}个真实样本 acc={acc:.2f} → {os.path.basename(path)}")
            trained += 1
    if trained == 0:
        print("没有(动作,错误)达到训练门槛。先跑 video_sim + ai_review 攒更多角度时序+标注。")
        print("查缺口: python train_aqa.py status")
    else:
        print(f"\n训练完成 {trained} 个时序错误分类器 → {MODEL_DIR}")
        print("注: 数据量仍小, acc 仅供管线验证参考; 数据上规模后指标才可信。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "train"], nargs="?", default="status")
    args = ap.parse_args()
    (status if args.cmd == "status" else train)()
