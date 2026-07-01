"""poc_synth2real.py — 能否缩小'合成训->真实测'的域差(不用新真实数据)

基线(上一实验): 全特征(111=99坐标+12角度/元) TCN 合成->真实 ~29%。
假设: 真实失败主因是 99 维归一化坐标带强合成偏置; 关节【角度】物理不变、跨域更稳。
便宜两招对比:
  V1 full      : 现有 111 维
  V2 angles    : 仅 8 个关节角(膝/髋/肘/肩 L+R)
  V3 angles+tilt: 8角 + 躯干倾角
  V4 angles+aug: V3 再加 噪声/幅度抖动/左右镜像 增广
全部 合成训 -> 14 真实测, 3 seed 平均。仅用现有数据。
"""
import os, sys, json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from poc_skeleton_model import load_clips, sample_fixed, build_tcn, LABELS

# make_features 布局: [flat 99 | a_knee_L,R, a_hip_L,R, a_elb_L,R, a_sho_L,R, torso_tilt, hip_y, sho_hip_y, vis_mean]
ANG8 = list(range(99, 107))          # 8 关节角
TILT = [107]                          # 躯干倾角
MIRROR_PAIRS = [(0, 1), (2, 3), (4, 5), (6, 7)]  # 角度子集内 L<->R


def slice_feats(clips, cols):
    out = []
    for c in clips:
        out.append({**c, "feats": c["feats"][:, cols]})
    return out


def to_XY(clips, F):
    X = np.stack([sample_fixed(c["feats"]) for c in clips]).astype(np.float32)  # (N,T,F)
    y = np.array([c["label"] for c in clips])
    return X, y


def train_test_tcn(train_clips, test_clips, F, seed, aug=False, n_angle=0):
    import torch, torch.nn as nn
    torch.manual_seed(seed); np.random.seed(seed)
    Xtr, ytr = to_XY(train_clips, F); Xte, yte = to_XY(test_clips, F)
    mu, sd = Xtr.mean((0, 1), keepdims=True), Xtr.std((0, 1), keepdims=True) + 1e-6
    Xtr = (Xtr - mu) / sd; Xte = (Xte - mu) / sd
    Xtr_t = torch.tensor(np.transpose(Xtr, (0, 2, 1)))   # (N,F,T)
    Xte_t = torch.tensor(np.transpose(Xte, (0, 2, 1)))
    ytr_t = torch.tensor(ytr)
    model = build_tcn(F, len(LABELS))
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    lossf = nn.CrossEntropyLoss()
    n = len(Xtr_t); bs = 32
    model.train()
    for ep in range(60):
        perm = torch.randperm(n)
        for i in range(0, n, bs):
            b = perm[i:i + bs]
            xb = Xtr_t[b].clone()
            if aug:
                xb = xb + torch.randn_like(xb) * 0.15            # 噪声
                xb = xb * (0.9 + 0.2 * torch.rand(xb.size(0), 1, 1))  # 幅度抖
                if n_angle and torch.rand(1).item() < 0.5:        # 左右镜像(仅角度子集)
                    xb2 = xb.clone()
                    for a, c in MIRROR_PAIRS:
                        if a < n_angle and c < n_angle:
                            xb2[:, a], xb2[:, c] = xb[:, c], xb[:, a]
                    xb = xb2
            opt.zero_grad(); loss = lossf(model(xb), ytr_t[b]); loss.backward(); opt.step()
    model.eval()
    with torch.no_grad():
        pred = model(Xte_t).argmax(1).numpy()
    return float((pred == yte).mean())


def run_variant(clips, cols, name, aug=False):
    synth = [c for c in clips if not c["is_real"]]
    real = [c for c in clips if c["is_real"]]
    s = slice_feats(synth, cols); r = slice_feats(real, cols)
    F = len(cols)
    accs = [train_test_tcn(s, r, F, seed, aug=aug, n_angle=(F if aug else 0)) for seed in (0, 1, 2)]
    print(f"  {name:20s} F={F:3d}  合成->真实 acc = {np.mean(accs)*100:.1f}% ± {np.std(accs)*100:.1f}")
    return float(np.mean(accs))


def main():
    clips = load_clips()
    print(f"clips={len(clips)} (real={sum(c['is_real'] for c in clips)})  "
          f"7类随机基线≈14%\n")
    print("合成训 -> 14 真实测 (TCN, 3 seed):")
    res = {}
    res["full_111"]      = run_variant(clips, list(range(111)),  "V1 full(111)")
    res["angles8"]       = run_variant(clips, ANG8,              "V2 angles(8)")
    res["angles_tilt9"]  = run_variant(clips, ANG8 + TILT,      "V3 angles+tilt(9)")
    res["angles_aug"]    = run_variant(clips, ANG8 + TILT,      "V4 angles+aug(9)", aug=True)
    print("\n小结:")
    print(f"  全特征 {res['full_111']*100:.1f}% -> 仅角度 {res['angles8']*100:.1f}% "
          f"-> 角度+倾角 {res['angles_tilt9']*100:.1f}% -> +增广 {res['angles_aug']*100:.1f}%")
    best = max(res.values())
    print(f"  最佳 {best*100:.1f}% (随机≈14%)。"
          + ("便宜招有效, 但要可用仍需真实数据。" if best < 0.6 else "提升显著。"))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "poc_synth2real_results.json")
    json.dump(res, open(out, "w"), indent=2)
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
