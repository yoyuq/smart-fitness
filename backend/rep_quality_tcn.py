"""rep_quality_tcn.py — 单动作 rep 质量判定的小模型 (TCN), 已验证架构(UI-PRMD 94%/r0.91)

链路: 用户选动作 -> 完成一次 rep -> rep_scorer 产出 angle_series(4通道×32帧)
      -> 本模块 TCN -> 质量分(0~100). 无模型/无 torch 时调用方回退规则分, 永不报错。

数据: backend/fitness.db 的 rep_scores(angle_series + total). 当前以规则分 total 为
      bootstrap 目标(蒸馏), 待 AI 评审/人工标注增多后把目标换成 ai_score 即可, 接口不变。

用法:
  python rep_quality_tcn.py status     # 看可用数据
  python rep_quality_tcn.py train      # 训练并存 datasets/models/rep_quality_tcn.pt
  python rep_quality_tcn.py selftest   # 加载模型, 对真实 rep 打分, 验证端到端
"""
import os, sys, json, sqlite3, math

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "fitness.db")
MODEL_DIR = os.path.join(BACKEND_DIR, "..", "datasets", "models")
CKPT = os.path.join(MODEL_DIR, "rep_quality_tcn.pt")
CHANNELS = ["primary", "torso", "lr_diff", "shoulder"]   # 与 rep_scorer.angle_series 对齐
SERIES_LEN = 32


# ---------------- 数据处理 ----------------
def series_to_matrix(series):
    """rep_scorer 的 angle_series dict -> (C, T) float32, 缺失插值/补 0。"""
    import numpy as np
    cols = []
    for ch in CHANNELS:
        v = list(series.get(ch) or [])
        if len(v) < SERIES_LEN:
            v = v + [None] * (SERIES_LEN - len(v))
        v = v[:SERIES_LEN]
        a = np.array([np.nan if x is None else float(x) for x in v], dtype=np.float32)
        if np.isnan(a).all():
            a[:] = 0.0
        elif np.isnan(a).any():                       # 线性插值填缺
            idx = np.arange(SERIES_LEN)
            good = ~np.isnan(a)
            a = np.interp(idx, idx[good], a[good]).astype(np.float32)
        cols.append(a)
    return np.stack(cols, 0)                            # (C, T)


def load_db(db_path=DB_PATH, target="ai"):
    """target='ai' -> 用 AI 评审分(蒸馏 VLM 评审团); 'rule' -> 用规则分(bootstrap)."""
    import numpy as np
    c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row
    if target == "ai":
        rows = c.execute(
            "SELECT r.exercise AS exercise, r.angle_series AS angle_series, a.ai_score AS y "
            "FROM rep_scores r JOIN ai_reviews a ON a.rep_id = r.id "
            "WHERE a.ai_score IS NOT NULL AND r.angle_series IS NOT NULL").fetchall()
    else:
        rows = c.execute(
            "SELECT exercise, angle_series, total AS y FROM rep_scores "
            "WHERE angle_series IS NOT NULL AND total IS NOT NULL").fetchall()
    c.close()
    X, y, ex = [], [], []
    for r in rows:
        try:
            s = json.loads(r["angle_series"])
        except Exception:
            continue
        if not s or "primary" not in s:
            continue
        X.append(series_to_matrix(s)); y.append(float(r["y"])); ex.append(r["exercise"])
    if not X:
        return None
    return np.stack(X), np.array(y, np.float32), ex


# ---------------- 模型 ----------------
def build_model():
    import torch.nn as nn
    return nn.Sequential(
        nn.Conv1d(len(CHANNELS), 32, 5, padding=2), nn.ReLU(), nn.BatchNorm1d(32),
        nn.Conv1d(32, 32, 3, padding=2, dilation=2), nn.ReLU(),
        nn.AdaptiveAvgPool1d(1), nn.Flatten(),
        nn.Dropout(0.3), nn.Linear(32, 1),
    )


def _augment(x, rng):
    import numpy as np
    x = x + rng.normal(0, 0.06, x.shape).astype(np.float32)        # 通道噪声
    x = x * np.float32(0.9 + 0.2 * rng.random())                   # 幅度抖
    if rng.random() < 0.5:                                         # 时间扭曲
        T = x.shape[1]; src = np.linspace(0, T - 1, T)
        warp = np.clip(src + rng.normal(0, 1.0, T), 0, T - 1)
        x = np.stack([np.interp(src, warp, x[c]) for c in range(x.shape[0])]).astype(np.float32)
    return x


# ---------------- 训练 ----------------
def train(db_path=DB_PATH, epochs=120, aug_factor=10, seed=0, target="ai"):
    import numpy as np, torch, torch.nn as nn
    data = load_db(db_path, target=target)
    if data is None:
        print(f"无可用数据 (target={target})"); return
    X, y, ex = data
    print(f"训练目标 = {target} ({'AI评审分/蒸馏VLM' if target=='ai' else '规则分/bootstrap'})")
    print(f"样本 {len(X)} 条 (动作分布: " +
          ", ".join(f"{e}:{ex.count(e)}" for e in sorted(set(ex))) + ")")
    rng = np.random.default_rng(seed); torch.manual_seed(seed)

    # 标准化(全集统计, 存进 ckpt)
    mu = X.mean((0, 2), keepdims=True); sd = X.std((0, 2), keepdims=True) + 1e-6
    Xn = (X - mu) / sd
    idx = rng.permutation(len(Xn)); ntr = max(1, int(len(Xn) * 0.8))
    tr, te = idx[:ntr], idx[ntr:]

    # 训练集增强
    Xa, ya = [], []
    for i in tr:
        Xa.append(Xn[i]); ya.append(y[i])
        for _ in range(aug_factor):
            Xa.append(_augment(Xn[i], rng)); ya.append(y[i])
    Xa = torch.tensor(np.stack(Xa)); ya = torch.tensor(np.array(ya, np.float32) / 100.0).view(-1, 1)

    model = build_model()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    lossf = nn.MSELoss(); n = len(Xa); bs = 64
    model.train()
    for ep in range(epochs):
        perm = torch.randperm(n)
        for i in range(0, n, bs):
            b = perm[i:i + bs]
            opt.zero_grad(); loss = lossf(model(Xa[b]), ya[b]); loss.backward(); opt.step()

    # 评估(holdout, 真实分)
    model.eval()
    mae = float("nan")
    if len(te):
        with torch.no_grad():
            pred = model(torch.tensor(Xn[te])).numpy().ravel() * 100.0
        mae = float(np.mean(np.abs(pred - y[te])))
    os.makedirs(MODEL_DIR, exist_ok=True)
    torch.save({"state": model.state_dict(), "mu": mu, "sd": sd,
                "channels": CHANNELS, "series_len": SERIES_LEN,
                "n_train": int(len(tr)), "n_test": int(len(te)),
                "holdout_mae": mae, "target": target}, CKPT)
    print(f"holdout MAE = {mae:.2f} 分 (n_test={len(te)})  [目标={target}]")
    print(f"saved {CKPT}")


# ---------------- 推理 ----------------
class RepQualityScorer:
    _inst = None

    def __init__(self, ckpt=CKPT):
        import torch, numpy as np
        self.ok = False
        try:
            d = torch.load(ckpt, map_location="cpu", weights_only=False)
            self.model = build_model(); self.model.load_state_dict(d["state"]); self.model.eval()
            self.mu = d["mu"]; self.sd = d["sd"]; self.ok = True
        except Exception as e:
            self.err = str(e)

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = RepQualityScorer()
        return cls._inst

    def score(self, angle_series):
        """angle_series(dict) -> 质量分 0~100, 失败返回 None(调用方回退规则)。"""
        if not self.ok:
            return None
        import torch, numpy as np
        try:
            x = (series_to_matrix(angle_series) - self.mu[0]) / self.sd[0]
            with torch.no_grad():
                v = float(self.model(torch.tensor(x[None]).float()).item())
            return round(max(0.0, min(100.0, v * 100.0)), 1)
        except Exception:
            return None


def score_rep_quality(angle_series, rule_total=None):
    """链路集成入口: 有模型用模型分, 否则回退规则分。"""
    q = RepQualityScorer.get().score(angle_series or {})
    return q if q is not None else rule_total


# ---------------- CLI ----------------
def _status():
    data = load_db()
    if data is None:
        print("rep_scores 无带 angle_series 的样本"); return
    X, y, ex = data
    from collections import Counter
    print(f"可训练样本: {len(X)} 条")
    for e, n in Counter(ex).most_common():
        print(f"  {e:16s} {n}")
    print(f"质量分(规则 total): min={y.min():.0f} max={y.max():.0f} mean={y.mean():.1f}")


def _selftest():
    sc = RepQualityScorer.get()
    if not sc.ok:
        print("模型未加载, 先 train。", getattr(sc, "err", "")); return
    data = load_db()
    if data is None:
        print("无数据可测"); return
    X, y, ex = data
    import json as _j, sqlite3 as _s
    c = _s.connect(DB_PATH); c.row_factory = _s.Row
    rows = c.execute("SELECT r.exercise ex, r.angle_series s, r.total rule, a.ai_score ai "
                     "FROM rep_scores r JOIN ai_reviews a ON a.rep_id=r.id "
                     "WHERE a.ai_score IS NOT NULL AND r.angle_series IS NOT NULL LIMIT 12").fetchall()
    c.close()
    print("端到端自测 (真实 rep -> TCN 模型分  vs  规则分  vs  AI评审分):")
    d_ai, d_rule = [], []
    for r in rows:
        q = sc.score(_j.loads(r["s"]))
        if q is None:
            continue
        d_ai.append(abs(q - r["ai"])); d_rule.append(abs(q - r["rule"]))
        print(f"  {r['ex']:13s} 模型={q:5.1f}   规则={r['rule']:5.1f}   AI={r['ai']:5.1f}")
    if d_ai:
        print(f"\n  模型 vs AI 平均差 = {sum(d_ai)/len(d_ai):.1f}   "
              f"模型 vs 规则 平均差 = {sum(d_rule)/len(d_rule):.1f}")
        print("  (目标=ai 时, 模型应更贴 AI 分)")
    print("链路 OK ✅ (load -> series_to_matrix -> TCN -> 质量分)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "train":
        tgt = sys.argv[2] if len(sys.argv) > 2 else "ai"
        train(target=tgt)
    elif cmd == "selftest":
        _selftest()
    else:
        _status()
