
# Smart Fitness v1.0 文件地图

```
smart_fitness/                          # 项目根目录
│
├── TEAM_REPORT.md                      # 📄 团队报告 (本文件)
├── DEPLOY.md                           # 📄 部署文档
├── RELEASE_NOTES.md                    # 📄 发版说明
├── FILE_MAP.md                         # 📄 文件地图
│
├── start.bat / start.sh / stop.sh      # 🔧 一键启动/停止
├── docker-compose.yml                  # 🔧 Docker 编排
├── Dockerfile                          # 🔧 Docker 构建
│
├── smart_fitness_v11_skfix.apk         # 📦 最新 APK (7.95MB)
│
├── ═══════════════════════════════════════════════════
├── 成员 A: 后端工程师
├── ═══════════════════════════════════════════════════
├── backend/
│   ├── main.py                         # FastAPI 入口 + startup 预热
│   ├── main_v2_routes.py               # 35 个 v2 核心路由
│   ├── main_v2_extra.py                # 高阶路由 (LLM/CSV/成就)
│   ├── ai_planner.py                   # LLM AI 计划生成器
│   ├── pose_engine.py -> ml_pose/       # 推理引擎引用
│   ├── requirements.txt                # Python 依赖
│   └── Dockerfile                      # 容器化构建
│
├── ═══════════════════════════════════════════════════
├── 成员 B: Android 工程师
├── ═══════════════════════════════════════════════════
├── android_app/app/src/main/
│   ├── java/com/smartfitness/app/
│   │   ├── MainActivity.kt             # 主程序入口
│   │   ├── api/
│   │   │   ├── ApiClient.kt            # Retrofit 客户端
│   │   │   └── ApiService.kt           # 40+ 接口定义
│   │   ├── model/
│   │   │   └── Models.kt               # 40+ 数据模型
│   │   └── ui/
│   │       ├── home/HomeFragment.kt    # 首页: 日历+成就
│   │       ├── training/
│   │       │   ├── TrainingFragment.kt # 训练核心 (骨架/HUD)
│   │       │   └── MjpegClient.kt      # MJPEG 流解析
│   │       ├── plans/PlansFragment.kt  # 计划管理+AI生成
│   │       └── profile/ProfileFragment.kt # 个人中心+CSV导出
│   └── res/
│       ├── layout/fragment_training.xml # 沉浸式训练布局
│       ├── values/colors.xml           # 12 色暗色主题
│       └── drawable/                   # 资源文件
│
├── ═══════════════════════════════════════════════════
├── 成员 C: 机器学习工程师
├── ═══════════════════════════════════════════════════
├── ml_pose/
│   ├── pose_engine.py                  # 推理引擎 (MediaPipe + 7类 + 评分)
│   ├── synth_dataset.py                # 合成数据生成器 (7类 × 30)
│   └── train_classifier.py             # RandomForest 训练脚本
│
├── datasets/models/
│   └── pose_classifier.pkl             # 训练模型 (33MB, 98% acc)
│
├── ═══════════════════════════════════════════════════
├── 成员 D: 嵌入式工程师
├── ═══════════════════════════════════════════════════
├── edge_esp32cam/
│   ├── esp32cam_fitness/
│   │   └── esp32cam_fitness.ino        # ESP32 固件 (400行 C++)
│   └── FLASH_GUIDE.md                  # 烧录指导
│
├── ═══════════════════════════════════════════════════
├── 成员 E: DevOps / QA
├── ═══════════════════════════════════════════════════
├── backend/
│   ├── test_v2_all.py                  # 全量 v2 接口测试
│   ├── _full_audit.py                  # 完整系统体检
│   ├── _perf.py                        # 性能基准测试
│   └── _ws_e2e.py                      # WS 端到端测试
│
├── ═══════════════════════════════════════════════════
├── 其他辅助文件
├── ═══════════════════════════════════════════════════
├── pc_simulator/                       # PC 摄像头模拟器
├── ai_vision/                          # 早期原型实验代码
├── quant/                              # 量化交易 (独立项目)
└── .git                                # Git 仓库 (tag v1.0)
```
