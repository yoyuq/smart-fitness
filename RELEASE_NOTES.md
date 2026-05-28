# Smart Fitness v1.0 Release Notes

**Release Date**: 2026-05-28
**Codename**: 收尾 (Final)

---

## 🎉 What's New

智能健身系统从 0 到 1 完整闭环上线。一台 ESP32-CAM + 一台 Android 手机 + 一台电脑，就能体验：实时姿态识别、动作自动分类、AI 教练点评、训练日历热图、AI 计划生成。

---

## ✨ Highlights

### 🤖 LLM 教练点评
训练完成后弹窗显示 AI 教练评语（火山方舟 / DeepSeek 双备），1.5s 出 40 字针对性反馈。

### 🧠 7 类动作自动识别
RandomForest 分类器 98% 精度，APP 边训边切动作 Spinner。

### 📊 Home 训练日历热图
GitHub 风格 12×7 格子，84 天活跃度一目了然。

### 🎯 Streak + 9 个成就
连续训练天数 + 首训练 / 百次俱乐部 / 30 次完美 / 全动作通关等成就。

### ⚡ AI 计划生成
Plans Tab 一键，LLM 30s 出 2 周计划，自动入库可练。

### 📋 CSV 数据导出
Profile 一键导出 365 天训练数据到手机 Downloads。

### 🖥 沉浸式训练 UI
全屏 ESP32 视频流 + 84sp 大数字 HUD + 浮层教练提示 + 单按钮训练。

---

## 📦 Backend

- FastAPI + SQLite + MediaPipe + LLM (Volc / DeepSeek)
- 52 个路由，35 个 v2 接口
- 推理 24ms 单线程 / 51 req/s 高并发 (10 并发 0 错误)
- 冷启动 3112ms → 54ms (startup 预热)
- ~300MB 内存，无 GPU 需求

### 新增接口
- `POST /api/v2/workout/summary` — 训练总结 + LLM 教练点评 + 徽章
- `GET /api/v2/stats/calendar` — 日历热图数据
- `GET /api/v2/stats/pb` — 个人最佳
- `GET /api/v2/stats/streak` — 连续训练天数
- `GET /api/v2/achievements` — 9 个成就
- `GET /api/v2/export/csv` — CSV 数据导出
- `POST /api/v2/ai/plan_generate` — AI 计划生成
- `POST /api/v2/training/start` / `stop` / `active` — 训练状态控制
- `POST /api/v2/vision/infer/full` — 全量推理 (关键点 + 角度 + 分类 + 评分)
- `POST /api/v2/ws/push` — 管理员 WS 推送

---

## 📱 Android App

- minSdk 24 / Android 7.0+
- APK 大小 7.95MB
- 7 个 Tab: Home / Training / Plans / History / Stats / Profile / Settings

### 训练流程
1. Training Tab → 选动作 → 开始
2. 全屏看 ESP32 视频流 + 实时 rep 计数 + form 评分
3. 停止 → 自动弹 LLM 教练点评 + kcal + 徽章
4. Home 看日历热图 + Streak 涨一格

---

## 🔌 ESP32-CAM 固件

- AI-Thinker ESP32-CAM
- 双通道：MJPEG @ 10fps (给 APP 看) + HTTP POST @ 2fps (给后端推理)
- ~400 行 C++

---

## 🧪 ML

- MediaPipe Pose (33 关键点)
- RandomForest 7 类: squat / push_up / plank / lunge / jumping_jack / bicep_curl / shoulder_press
- 训练精度: 98%
- 模型大小: 33MB
- 合成数据生成器: 220 段关键点序列

---

## 📈 实测性能

| 指标 | 数值 |
|------|------|
| 推理延迟 (单线程) | 24ms |
| 并发吞吐 (10 并发) | 51 req/s, 0 错误 |
| LLM 点评 | 1.5s |
| 冷启动 | 3112ms → 54ms |
| 后端内存 | ~300MB |
| APK 大小 | 7.95MB |

---

## 🚀 一键启动

### Windows
```cmd
start.bat
```

### Linux / macOS
```bash
./start.sh
```

启动后：
- 后端: http://localhost:8080
- APK 下载: http://localhost:8090/smart_fitness_v10_csv.apk

详见 `DEPLOY.md`。

---

## 📂 项目结构

```
smart_fitness/
├── backend/              # FastAPI 后端 (~3000 行 Python)
├── android_app/          # Kotlin APP (~2500 行)
├── edge_esp32cam/        # ESP32 固件 (~400 行 C++)
├── ml_pose/              # ML 训练 + 推理 (~600 行)
├── datasets/models/      # 训练好的分类器
├── pc_simulator/         # PC 摄像头模拟器
├── DEPLOY.md             # 部署文档
├── RELEASE_NOTES.md      # 本文件
├── start.bat / start.sh  # 一键启动
└── smart_fitness_v10_csv.apk  # 最新 APK
```

---

## 🙏 致谢

- MediaPipe (Google)
- 火山方舟 / DeepSeek (LLM)
- AI-Thinker (ESP32-CAM)
- 用户 hjl 的连续 14 小时实战陪练

---

## 🛣 后续可能方向 (v1.1+)

- BLE 心率监测
- 完整 Profile (用户偏好 / 目标 / 历史曲线图)
- 弱网模式 (离线缓存)
- 视频教程内嵌
- 多 ESP32 协同 (多角度同步)
- 30 天挑战模式
- 社交对战

---

**v1.0 OK. 收工!** 🎉
