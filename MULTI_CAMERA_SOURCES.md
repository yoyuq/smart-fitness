# Smart Fitness 多摄像头来源说明

本版本把训练摄像头从单一 ESP32-CAM 扩展为三种来源：

1. ESP32-CAM
2. Phone Camera（手机本机摄像头）
3. PC Camera（电脑摄像头瘦客户端）

核心原则：**图像在摄像头所在设备端 resize + JPEG 压缩，再上传到后端统一推理/计数。**

## 统一后端接口

所有来源最终都调用：

```http
POST /api/v2/vision/infer/full
```

请求核心字段：

```json
{
  "image": "base64 jpeg",
  "device_id": "esp32cam-001 | phone-xxx | pc-camera-001",
  "exercise": "squat",
  "source": "esp32cam | phone | pc",
  "backend": "mediapipe"
}
```

返回核心字段：

```json
{
  "detected": true,
  "rep_count": 3,
  "form_score": 86,
  "feedback": "...",
  "source": "phone",
  "device_id": "phone-..."
}
```

## APP 训练页

底部新增 Camera Source 选择：

- ESP32-CAM：连接 `http://<esp32_ip>:81/stream`，APP 抽帧压缩上传。
- Phone Camera：使用 CameraX 本机摄像头，手机端 resize/JPEG 压缩后上传。
- PC Camera：APP 不采集画面，只订阅后端 WS/HUD；PC 端脚本负责采集上传。

设置按钮里可配置：

- ESP32 IP
- ESP32 device_id
- PC camera device_id

Phone Camera device_id 自动生成：`phone-<android-device-id>`。

## 启动 PC Camera

先启动后端：

```powershell
cd C:\Users\hjl\.openclaw\workspace\smart_fitness\backend
python -m uvicorn main:app --host 0.0.0.0 --port 8080
```

再启动 PC 摄像头 agent：

```powershell
cd C:\Users\hjl\.openclaw\workspace\smart_fitness
python pc_simulator\pc_camera_agent.py --server http://127.0.0.1:8080 --device-id pc-camera-001 --exercise squat --preview
```

如果 APP 中 PC device_id 改了，脚本 `--device-id` 必须一致。

## 注意事项

- `training/start` 和 `/vision/infer/full` 必须使用同一个 `device_id`，否则后端 active training 匹配不上。
- 切换 Camera Source 时，APP 会停止之前来源的 MJPEG/CameraX 资源并重置本地 reps。
- Phone/PC 来源的压缩参数：
  - Phone Camera：maxWidth=640, JPEG quality=58, interval=500ms
  - PC Camera：默认 maxWidth=640, JPEG quality=60, interval=500ms
- ESP32 来源仍然依赖 ESP32 的 MJPEG 流。换网络时通常要重填 ESP32 IP。
