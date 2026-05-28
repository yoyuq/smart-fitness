# Smart Fitness — 智能健身系统

> 一部手机 + 一台电脑 + 一块 ESP32-CAM 摄像头 = 你的 AI 健身教练

---

## 🎯 这是做什么的？

**Smart Fitness** 是一套完整的 AI 辅助健身系统。你站在 ESP32-CAM 摄像头前做动作，系统实时：

1. **识别你在做什么动作**（深蹲 / 俯卧撑 / 平板支撑 / 弓步 / 开合跳 / 弯举 / 肩推）
2. **评价动作标不标准**（0～100 分，并给出文字纠正）
3. **计数 + 计时**（一组做了多少次、用了多久）
4. **训练结束弹 AI 教练点评**（DeepSeek / 火山方舟 LLM，1.5 秒出 40 字评语）
5. **记录一切**（日历热图、连续训练天数、9 个成就、CSV 导出）
6. **AI 帮你生成训练计划**（说目标，等 30 秒出 2 周计划）

---

## 🏗️ 系统长什么样

```
┌──────────────┐     MJPEG 视频流      ┌──────────────────┐
│  ESP32-CAM   │ ──────────────────▶   │   Android APP    │
│  (你面前)     │                       │  (你手机屏幕)     │
│              │   JPEG → POST         │                  │
│ 拍照→压缩→发   │ ──────────────────▶   │ 实时骨架叠加      │
│              │                       │ reps/form HUD    │
│              │                       │ LLM教练弹窗       │
│              │  ◀──────────────────  │ 训练控制信号      │
│              │   next_interval_ms    │                  │
└──────────────┘                       └────────┬─────────┘
                                                │
                                                ▼
                                       ┌──────────────────┐
                                       │  Backend 服务器   │
                                       │  (你电脑)          │
                                       │                   │
                                       │ MediaPipe 姿态推理 │
                                       │ 7类动作分类器 98%  │
                                       │ 评分规则引擎      │
                                       │ 数据库 SQLite     │
                                       │ WebSocket 推送    │
                                       │          ↓        │
                                       │  LLM 教练点评     │
                                       │  (火山方舟/DeepSeek)│
                                       └──────────────────┘
```

---

## 📜 项目流程（4 天开发流水线）

### Day 1 — 搭骨架

| 做了啥 | 谁做的 |
|--------|--------|
| FastAPI 后端 + SQLite 数据库 + JWT 登录 | 后端工程师 |
| Android 工程初始化 + 7个Tab框架 + Retrofit 网络层 | APP 工程师 |
| MediaPipe 单帧姿态推理验证跑通 | ML 工程师 |
| ESP32-CAM 拍照 + MJPEG 视频流推送到手机浏览器 | 嵌入式工程师 |

> Day 1 结束时：后端能注册登录，APP 能显示 7 个空页面，摄像头能在手机浏览器看到画面。

### Day 2 — 核心链路跑通

| 做了啥 | 谁做的 |
|--------|--------|
| 35 个 v2 API 路由 + WebSocket coach 频道 | 后端 |
| APP 训练页: MJPEG 视频流 + Canvas 骨架点绘制 | APP |
| 合成 150 段训练数据 + 训练 5 类 RandomForest 分类器 (96%) | ML |
| ESP32 双通道固件: MJPEG 10fps + HTTP POST 2fps | 嵌入式 |

> Day 2 结束时：站在摄像头前做动作，手机上能看到骨架点跟着动。

### Day 3 — 功能爆发

| 做了啥 | 谁做的 |
|--------|--------|
| LLM 教练点评 + CSV 导出 + 成就/PB/Streak 系统 | 后端 |
| APP 沉浸式 UI 重做 (全屏视频 + 大数字 HUD + 浮层教练条) | APP |
| 扩到 7 类动作 + AI 计划生成 + 训练日历热图 | APP |
| 扩训练数据到 220 段 + 7 类分类器 98% + 7 套评分规则 | ML |
| ESP32 真机联调通过 | 嵌入式 |

> Day 3 结束时：完整闭环 — 选动作 → 开始 → 自动识别 → 计数组数 → 停 → 弹 LLM 教练评语 → 日历亮格子。

### Day 4 — 收尾交付

| 做了啥 | 谁做的 |
|--------|--------|
| WS 端到端测试 + 并发压力测试 (51 req/s) + 全路由验证 | DevOps |
| 部署文档 + 一键启动脚本 + Git tag v1.0 | DevOps |
| 预览骨架 (不点开始也能看到蓝色骨架确认站位) | APP |
| 团队报告 + 本说明 + 打包交付 | DevOps |

---

## 📦 包里有什么

解压后：

```
smart_fitness_v1.0.zip
│
├── README_先看这个.md              ← 📖 你现在看的这份
├── DEPLOY.md                       ← 📖 部署教程 (装什么、怎么装、常见问题)
├── RELEASE_NOTES.md                ← 📖 发版更新说明
├── TEAM_REPORT.md                  ← 📖 5人团队分工 + 完整开发流程
│
├── start.bat                       ← 🚀 Windows 双击启动
├── docker-compose.yml              ← 🐳 Docker 部署
│
├── smart_fitness_v11_skfix.apk     ← 📱 手机直接装 (7.95MB)
│
├── backend/                        ← 🐍 后端源码
│   ├── main.py                     ← 启动入口
│   ├── main_v2_routes.py           ← 35个核心路由
│   ├── main_v2_extra.py            ← LLM/CSV/成就等高阶路由
│   ├── ai_planner.py               ← AI计划生成器
│   ├── requirements.txt            ← Python依赖
│   └── Dockerfile                  ← Docker构建
│
├── src/                            ← 📱 Android APP 源码 (Kotlin)
│   ├── main/java/...
│   │   ├── api/                    ← Retrofit 网络层
│   │   ├── model/                  ← 数据模型
│   │   └── ui/                     ← 7个Tab页面
│   └── res/                        ← 布局/主题/资源
├── build.gradle.kts
├── settings.gradle.kts
├── gradle.properties
├── gradlew.bat                     ← Android 构建脚本
│
├── ml_pose/                        ← 🧠 机器学习
│   ├── pose_engine.py              ← 推理引擎
│   ├── synth_dataset.py            ← 合成数据生成器
│   └── train_classifier.py         ← 分类器训练
│
├── datasets/models/                ← 训练好的模型
│
└── edge_esp32cam/                  ← 🔌 ESP32-CAM 固件
    ├── esp32cam_fitness.ino        ← 主程序 (C++, 400行)
    └── FLASH_GUIDE.md              ← 烧录指导
```

---

## 🚀 快速体验（3 分钟）

### 你需要
- **一台电脑**（Windows / Linux / Mac，能联网就行）
- **一部 Android 手机**
- **一块 ESP32-CAM**（可选，没有也能用 PC 摄像头模拟）

### 步骤

**第 1 步：启动后端**
```
Windows: 双击 start.bat
Linux:   ./start.sh
```

**第 2 步：手机装 APK**
手机浏览器打开：
```
http://你的电脑IP:8090/smart_fitness_v11_skfix.apk
```

**第 3 步：注册登录**
打开 APP → 注册账号 → 登录

**第 4 步：开练**
Training Tab → 选动作 → 按 Start → 对着摄像头做！

---

## 📊 性能数据（实测算出来的）

| 指标 | 数值 |
|------|------|
| 动作识别 | 7 类，98% 准确率 |
| 骨架推理 | **24ms** (单帧，CPU) |
| 并发能力 | **51 帧/秒** (10 并发，0 错误) |
| LLM 点评 | 1.5 秒出评语 |
| 后端内存 | ~300MB |
| APK 大小 | 7.95MB |
| 视频帧率 | MJPEG 10fps |
| 推理频率 | 2fps (训练中) / 0.2fps (待机) |
| 硬件需求 | **不需要 GPU** |

---

## 👥 团队分工

| 角色 | 负责 |
|------|------|
| 🧑‍💻 **后端工程师** | 服务器 API、数据库、认证、部署 |
| 🧑‍💻 **Android 工程师** | 手机 APP、UI 交互、骨架绘制 |
| 🧑‍💻 **机器学习工程师** | 姿态识别模型、合成数据、评分算法 |
| 🧑‍💻 **嵌入式工程师** | ESP32-CAM 固件、双通道通信 |
| 🧑‍💻 **DevOps / QA** | 测试、部署文档、一键启动、打包 |

---

## 🔗 更多资料

| 文件 | 内容 |
|------|------|
| `DEPLOY.md` | 从零开始部署，每步都有命令 |
| `RELEASE_NOTES.md` | 完整发版更新日志 |
| `TEAM_REPORT.md` | 团队报告 + 详细开发流程 + 指标汇总 |

---

> **Smart Fitness v1.0** — 2026 年 5 月 28 日发布
>
> 5 人 · 4 天 · 59 个子任务 · 6500 行代码 · 零 GPU · 总成本 ~¥50（仅硬件）
