# Smart Fitness v1.0 部署文档

> 一台 Windows / Linux 电脑 + 一块 ESP32-CAM + 一台 Android 手机 = 完整智能健身系统

## 系统架构

```
[ESP32-CAM]  --MJPEG--> [Android APP]
     |                       |
     +---POST JPEG---> [Backend :8080] <--WS--+
                         |       |            |
                         |    [SQLite]        |
                         |                    |
                         +--LLM call--> [Volc/DeepSeek]
```

- **后端**: Python 3.10+ FastAPI + SQLite + MediaPipe + LLM
- **APP**: Android 7+ Kotlin Retrofit Material 3
- **边缘**: ESP32-CAM (AI-Thinker) + OV2640

---

## 1. 后端部署

### 环境
- Python 3.10 或 3.11
- ~500MB 磁盘
- ~300MB 内存
- 可选：4 核 CPU 跑推理 (不需要 GPU)

### 安装

```bash
cd backend
pip install -r requirements.txt
# 可选: 启用 LLM 教练点评
export VOLC_ARK_API_KEY="<your_key>"
export DEEPSEEK_API_KEY="<your_key>"

# 启动
python -m uvicorn main:app --host 0.0.0.0 --port 8080
```

启动后会看到：
```
[startup] pose_engine warmup OK (cold start 3112ms -> 54ms)
[startup] classifier loaded: 7 classes
INFO:     Uvicorn running on http://0.0.0.0:8080
```

### 验证
```bash
curl http://localhost:8080/health
# {"status":"ok","version":"v2"}
```

### Docker 启动 (可选)
```bash
docker compose up -d
```

---

## 2. ESP32-CAM 烧录

### 硬件
- AI-Thinker ESP32-CAM
- USB-TTL 下载器 (CH340 / CP2102)

### 固件
路径: `edge_esp32cam/esp32cam_fitness/esp32cam_fitness.ino`

修改 WiFi 配置：
```cpp
const char* WIFI_SSID = "<your_ssid>";
const char* WIFI_PASS = "<your_password>";
const char* SERVER_IP = "<backend_ip>";  // 例: 192.168.72.56
const int SERVER_PORT = 8080;
```

烧录步骤见 `edge_esp32cam/FLASH_GUIDE.md`。

烧好后串口会输出：
```
[wifi] connected: 192.168.72.20
[mjpeg] server started on :81/stream
[upload] POST /api/v2/vision/infer/full @ 2fps
```

---

## 3. Android APP 安装

最新版本：**smart_fitness_v10_csv.apk** (7.95MB)

### 方式 1: HTTP 下载
后端启动后会附带 HTTP 文件服务 :8090，手机浏览器打开：
```
http://<backend_ip>:8090/smart_fitness_v10_csv.apk
```

### 方式 2: adb 安装
```bash
adb install -r smart_fitness_v10_csv.apk
```

### 首次使用
1. 注册账号 (用户名 + 密码)
2. Profile → 设置 ESP32 IP (默认 192.168.72.20)
3. Training Tab → 选动作 → 开始训练
4. 训练结束自动弹 LLM 教练点评

---

## 4. 端口清单

| 端口 | 用途 |
|------|------|
| 8080 | 后端 HTTP + WS |
| 8090 | APK / 静态资源下载 |
| 81 | ESP32-CAM MJPEG 直推 |

---

## 5. 常见问题

### 后端冷启动慢？
首次推理会加载 MediaPipe (~3s)。已加入 startup 预热，二次推理 < 60ms。

### ESP32 上传失败？
- 检查 WiFi 是否同网段
- 检查后端 IP 是否填对
- 检查后端是否监听 0.0.0.0 (不是 127.0.0.1)

### LLM 点评没出来？
未设置 API key 时会回退到规则模板。设置任一即可：
- `VOLC_ARK_API_KEY` (火山方舟)
- `DEEPSEEK_API_KEY` (DeepSeek)

### APP 装完登录失效？
`adb install -r` 会清 SharedPreferences。重新登录即可。

---

## 6. 关停

```bash
# Windows
netstat -ano | findstr :8080
taskkill /PID <pid> /F

# Linux
pkill -f "uvicorn main:app"
```

---

## 7. 性能指标 (实测)

| 指标 | 数值 |
|------|------|
| 推理延迟 (单线程) | 24ms avg |
| 并发吞吐 | 51 req/s (10 并发) |
| LLM 点评 | 1.5s avg |
| 冷启动 | 3.1s → 54ms (预热后) |
| 后端内存 | ~300MB |
| APK 大小 | 7.95MB |
| ESP32 帧率 | MJPEG 10fps + 推理 2fps |
| 分类器精度 | 98% (7 类) |

---

## 8. 模型列表

`datasets/models/pose_classifier.pkl` (33MB)
- 7 类: squat / push_up / plank / lunge / jumping_jack / bicep_curl / shoulder_press
- 算法: RandomForest (sklearn)
- 训练数据: 220 段合成关键点序列
- 准确率: 98% (held-out)
