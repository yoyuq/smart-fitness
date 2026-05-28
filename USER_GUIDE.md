# Smart Fitness 智能运动指导系统 — 用户指南

> 5 步从硬件到第一次运动评分

## 1. 装备清单

### 必需
- **ESP32-CAM** (AI-Thinker 版本，约 ¥35) — 拍照采集
- **USB 转 TTL 下载器** (CH340 或 CP2102, 约 ¥10) — 烧录固件
- **5V 电源** (Micro-USB 或电池盒) — ESP32 供电
- **WiFi 路由器** — ESP32 与服务器同网

### 可选
- 三脚架或墙挂支架 — 固定摄像头视角
- LED 灯 — 提升弱光识别精度

## 2. 安装后端

```bash
# 方式 A: Docker (推荐)
export DEEPSEEK_API_KEY=sk-xxx     # 启用 LLM 教练
docker compose up -d

# 方式 B: 直接跑 Python
cd backend
pip install -r requirements.txt
export DEEPSEEK_API_KEY=sk-xxx
python main.py
```

检查: 浏览器打开 http://你的IP:8080/health 应该看到 status:healthy

## 3. 注册账号 + 绑定 ESP32

1. 安装 Android App (从 android_app/ 编译 APK)
2. 注册 → 登录
3. **Profile 页 → 记录体重 / 身高** (用于 BMI 和强度推荐)
4. **Profile 页 → 绑定 ESP32 设备** → 输入 device_id (例: esp32-001) → 系统返回 32 字符 token
5. **复制 token**

## 4. 烧录 ESP32 固件

位置: edge_esp32cam/esp32cam_fitness/esp32cam_fitness.ino

修改顶部配置 (无引号问题):
- WIFI_SSID 填你家 WiFi 名
- WIFI_PASS 填 WiFi 密码
- SERVER_HOST 填后端机 IP (例 192.168.1.100)
- DEVICE_TOKEN 填上一步复制的 token

上传方式: Arduino IDE 或 PlatformIO

## 5. 开始训练

1. 把 ESP32-CAM 对准训练区 (摄像头能看到全身)
2. App → Training 页 → 输入 session_id → Connect
3. 开始深蹲 / 俯卧撑 / 平板支撑
4. App 实时显示:
   - 运动类型 (squat / push_up / plank ...)
   - 计数 (1, 2, 3...)
   - 评分 (0-100)
   - LLM 教练提示 (动作错误时 30 字中文)
   - TTS 自动播报 (中文)

## 性能基准 (F-02)

- 10 设备 × 5 fps = 50 qps
- p50: 25 ms, p95: 80 ms, **p99: 171 ms**
- 成功率 100%

## 数据隐私

- 原始图片不存储 (推理后立即丢弃)
- 关键帧 30 天后清理
- 统计永久保留 (你的数据，可随时导出)
- API 调用必须带 device token 或 JWT，禁止跨用户写入

## 故障排查

| 问题 | 解决 |
|---|---|
| ESP32 连不上 | 检查 WiFi SSID/密码，确认在同一局域网 |
| 推理 detected=False | 摄像头能看到全身？光线够吗？ |
| LLM coach_tip 总是 null | DEEPSEEK_API_KEY 设了吗？ |
| 安卓 WS 断连 | 网络变化 / app 后台被杀 → 重连 |
| 评分一直 100 | 动作太标准 (好事) 或 form_analyzer 还没识别到具体问题 |

## 高级

- 自定义运动计划: Plans 页
- 查看历史: Sessions 页
- 多设备绑定: 同一账号可绑多台 ESP32
- 数据导出: GET /api/v2/exercise/log?days=90
- 压力测试: cd backend; python stress_test.py --workers 10 --fps 5