# 评分系统 V2 计划书（2026-06-12）

> 目标：把当前"单帧角度规则提醒器"升级为"可信的动作成绩系统"。
> 背景问题：单帧评分被站立帧稀释、阈值无标定、视角依赖、维度单一；
> "拍到脸给满分"已由可见度门禁修复（344affc），本计划解决剩余的结构性问题。
> 标定路线采用 AI 评审团而非人工教练打分（规避标注者主观偏差），
> 参考 FLEX(arXiv:2506.03198) 的结构化错误清单方法。

---

## 第一阶段：按 rep 评分（本次实施）

**原则：评分单位从"帧"改为"一次动作"。**

### 设计

```
帧流 → RepDetector(现有状态机, 判定 up/down 相位) 
     → RepScorer 累积本次动作期间的角度时序
     → rep 完成瞬间结算: 
          depth    深度分  = 主角度极值 vs 动作目标区间
          control  控制分  = rep 时长 + 下放/上举时长比 (太快=自由落体)
          symmetry 对称分  = 动作期间左右主角度差均值 (复用已有对称计算)
          total    = 0.5*depth + 0.3*control + 0.2*symmetry
     → 会话成绩 = 各 rep total 的均值 (不再混入站立帧)
```

### 各动作主角度与目标区间（首版规则，待第三阶段标定）

| 动作 | 主角度 | 满分深度区间 | 控制时长合理区间 |
|---|---|---|---|
| squat | 膝角极小值 | 70–100° | 1.5–6s |
| push_up | 肘角极小值 | 60–90° | 1–5s |
| lunge | 前膝极小值 | 80–110° | 1.5–6s |
| shoulder_press | 肘角极大值 | 160–180° | 1–5s |
| bicep_curl | 肘角极小值 | 30–60° | 1–5s |
| jumping_jack | 肩角极大值 | 150–180° | 0.4–2s |
| plank | （静态动作，保持帧评分均值，另计稳定度=髋角方差） | — | — |

### 落地点

- 新模块 `backend/rep_scorer.py`：`RepScorer` 类（与 detector 同生命周期，按 device+exercise 维护）
- `main_v2_routes.py` 推理路径：有效帧喂给 RepScorer；rep 完成时响应中携带 `rep_score` 分项
- 新表 `rep_scores(session_id, rep_index, ts, exercise, depth, control, symmetry, total, peak_angle, duration_s, feedback)`
- `training/stop` 会话结算：`avg_form_score = AVG(rep_scores.total)`，无 rep 时回退旧帧均值
- HUD：训练中 `form_score` 字段改为"上一个 rep 的 total"（帧级反馈仍然实时显示 feedback 文本）
- 无效帧（门禁拦截）不进 RepScorer

### 验收

- 合成数据回放：标准深蹲序列 rep 分 ≥85；半程蹲 depth 显著低；快速反弹蹲 control 显著低
- 站立 30 秒 + 5 个标准蹲：会话分≈rep 均分，不被站立稀释（对照旧逻辑差异）
- pytest 套件新增 rep_scorer 用例

---

## 第二阶段：姿态后端切 YOLO26（次周）

- 服务器推理从 MediaPipe 切到 `yolo26n/s-pose`（ultralytics 8.4.50 已装，多人+免NMS）
- 复用 `ai_vision/pose_engine.py` 既有 YOLO 后端与 COCO17→MP33 升格层
- 关节可见度→keypoint confidence 映射，门禁阈值重标
- 多人场景：按人分配 track id，预留"一摄像头多用户"绑定（健身房部署核心）
- CPU 环境用 n 档兜底，GPU 上 m 档；MediaPipe 保留为 fallback 后端
- 验收：门禁/评分/计数回归全绿；PoC 多人视频每人独立计数评分

## 第三阶段：AI 评审团标定管线（两周内启动）

- 训练时自动留存每个 rep 的 3 关键帧（最深点/起始/结束，已有 pose_data 落库基础）
- 夜间批处理：qwen3-vl-plus 按**结构化错误清单**逐项勾选（髋是否低于膝/膝是否内扣/躯干前倾是否>45°…），不打总分
- 多模型（qwen3-vl + Gemini 可用时）× 多提示词，取中位数 → 一致性标签
- 标签回流：回归校准第一阶段的目标区间与权重；评审结果入 `ai_review` 表可追溯
- 产出：每月一版阈值校准报告（误差分布、规则与评审团分歧 top 案例）

## 第四阶段：时序评分模型（数据攒够后）

- 特征：rep 内关节角度时序（重采样到固定长度）
- 模型：轻量 TCN/GRU，多头输出分项分；FLEX 数据集预训练（注意 CC BY-NC-SA，仅研究验证）
- 训练标签：第三阶段评审团积累的标定数据（商用安全）
- 部署：ONNX，服务器毫秒级；规则分作为先验与回退

## 风险与依赖

- YOLO26 CPU 推理慢（PoC 实测 m 档 1.4s/帧）：健身房服务器需 GPU，开发期用 n 档
- VLM 评审成本：用关键帧而非视频，百炼免费额度内；额度耗尽切 hunyuan-vision
- FLEX/Fitness-AQA 许可均为非商业：只用于预研，不进商用模型训练集
- plank 等静态动作不适用 rep 模型：保留帧评分路径
