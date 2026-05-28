# ESP32-CAM 烧录与联调指南

> 适用：AI-Thinker ESP32-CAM + USB-TTL 下载器（CH340/CP2102 都行）
> 板子：v2 代码 esp32cam_fitness.ino（含 A-02 ~ A-10 全部能力）

## 1. 准备

### 1.1 Arduino IDE
- 安装 Arduino IDE 2.x
- 文件 → 首选项 → 附加开发板管理器网址：
  `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
- 工具 → 开发板管理器 → 搜索 esp32 → 安装 **esp32 by Espressif Systems** 3.x
- 工具 → 开发板 → ESP32 → **AI Thinker ESP32-CAM**
- 工具 → CPU Frequency: 240MHz
- 工具 → Flash Frequency: 80MHz
- 工具 → Flash Mode: QIO
- 工具 → Partition Scheme: **Huge APP (3MB No OTA / 1MB SPIFFS)**
- 工具 → 端口：选 USB-TTL 出现的 COMx

### 1.2 接线（烧录态）
| ESP32-CAM | USB-TTL |
|-----------|---------|
| 5V        | 5V      |
| GND       | GND     |
| U0R       | TX      |
| U0T       | RX      |
| IO0       | GND     |  ← 烧录必须接地

烧录完成后 **断开 IO0-GND**，按 RST 复位即可正常运行。

## 2. 烧录步骤

1. 打开 `edge_esp32cam/esp32cam_fitness/esp32cam_fitness.ino`
2. 修改以下三处：
   ```cpp
   const char* WIFI_SSID = "你的WiFi";
   const char* WIFI_PASS = "WiFi密码";
   const char* SERVER_HOST = "192.168.x.x";   // 后端电脑 IP
   ```
3. 可选：先去后端绑定设备拿 token（见 §4），把 `DEVICE_TOKEN` 填上
4. 点击 → 上传（烧录约 30s）
5. 烧完拔 IO0，按 RST，打开串口监视器（115200 baud）

## 3. 看串口判断状态

正常启动流程：
```
================================================
 Smart Fitness ESP32-CAM v2 (Sprint 2)
================================================
Device ID:    esp32cam-001
Server:       http://192.168.x.x:8080/api/v2/vision/infer/full
Heap free:    ...
PSRAM found:  yes
[CAM] init ok, 320x240 JPEG q=12, fb_count=2, location=PSRAM
[WiFi] connecting to xxxx ...
[WiFi] connected, IP=192.168.x.y RSSI=-50 dBm
[OTA] Ready, hostname=esp32cam-fitness
[READY] Entering capture loop
[#1] frame=14523B q=12 sim=0% motion ok
[HTTP] 200 ok, 1024B resp
```

板载 LED（GPIO33 红色）：
- 启动成功：闪 2 次
- 每次拍照：闪 1 次（短）
- 上传成功：闪 2 次（短）
- 出错：闪 5 次
- 进入低功耗：长亮 200ms 后熄灭

## 4. 设备绑定（拿 token）

第一次让设备走严格鉴权前，先在后端绑：

```bash
# 1. 注册/登录后拿到 user token
curl -X POST http://SERVER:8080/api/v2/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"YOU","password":"YOUR_PASS"}'
# 返回里的 access_token

# 2. 绑定 device_id 给当前用户
curl -X POST http://SERVER:8080/api/v2/devices/bind \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer USER_ACCESS_TOKEN" \
  -d '{"device_id":"esp32cam-001","name":"客厅摄像头"}'
# 返回 { ok: true, device_id, token: "abc...xyz" }
```

把 `token` 填进 sketch 的 `DEVICE_TOKEN`，重烧。

## 5. OTA 远程升级（A-08）

第一次必须用 USB 烧。之后只要同一 WiFi：
- Arduino IDE → 工具 → 端口 → 选 **esp32cam-fitness at 192.168.x.y**
- 密码：`fit2026`（在 sketch 顶部 `OTA_PASSWORD`）
- 上传即可

## 6. 联调测试清单

### 6.1 单机自测
- [ ] 串口能看到 `[READY]`
- [ ] LED 启动闪 2 下
- [ ] 看到周期性 `[#N] frame=... HTTP 200 ok`

### 6.2 后端接收验证
后端日志（uvicorn 控制台）应有：
```
INFO: ... "POST /api/v2/vision/infer/full HTTP/1.1" 200
```

### 6.3 全链端到端（F-01）
1. 后端 8080 起好
2. 安卓 app 登录同一用户，TrainingFragment 留空 session_id，连接 coach 通道
3. ESP32 通电
4. 镜头对准你，做 10 个深蹲
5. 安卓应实时看到 `运动: squat reps=N 评分=XX`，且会 TTS 播报错误纠正

### 6.4 节流验证（A-04）
画面无人时，串口应看到：
```
[A-04] interval 500 -> 1500 ms (server hint)
```
有人时回到 500ms。

### 6.5 帧差分验证（A-05）
对着静止画面，串口应大量 `skip (similarity=99% > 95%)`，HTTP 调用大幅减少。

### 6.6 大小守卫验证（A-03）
对着复杂场景或亮光，frame 可能 >30KB，串口打印 `lower quality -> 15` 后回归。

### 6.7 低功耗验证（A-10）
30 秒无运动后，串口：`[A-10] no motion ... light sleep 5s`，LED 长亮 200ms 后熄灭。挪动画面即唤醒。

## 7. 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| `[CAM] init failed: 0x20004` | 摄像头模块没插紧/坏了 | 重插，确认 OV2640 模块到位 |
| `Brownout detector was triggered` | 5V 供电不足 | 用独立 5V/2A 电源，别用电脑 USB |
| `[WiFi] FAILED` 3 次 | SSID/密码/信号弱 | 改 WiFi 配置；2.4GHz only |
| 串口乱码 | 波特率不对 | 改 115200 |
| HTTP 401 | strict auth + 没填 DEVICE_TOKEN | 走 §4 绑定流程 |
| HTTP 429 | 上传超过 8 fps | sketch 已自动降；后端可调 `FITNESS_RATE_MAX` |
| OTA 找不到设备 | 不在同一网段/防火墙拦了 mDNS | 用 IP 直连：`espota.py -i 192.168.x.y -p 3232 -a fit2026 -f firmware.bin` |
