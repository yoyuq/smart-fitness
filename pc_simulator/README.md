# PC 视觉模拟器 — Smart Fitness

替代 STM32 摄像头模块的 PC 端模拟器。调用 PC 摄像头进行 MediaPipe 姿态估计，通过 MQTT 上报到服务器。

## 架构

```
PC 摄像头 → OpenCV → PoseEngine → MQTT Publisher → MQTT Broker → FastAPI Backend
                                 ↓
                    (可选) 实时预览窗口 + 骨骼叠加
```

## 用法

```bash
# 安装依赖
pip install -r requirements.txt

# 基本启动（无预览，仅后台上报）
python pc_simulator.py

# 带预览窗口
python pc_simulator.py --show-preview

# 指定 MQTT broker 和设备 ID
python pc_simulator.py --broker 192.168.123.56:1883 --device-id my-pc-001

# 连接到后端 API（自动创建训练 session）
python pc_simulator.py --server-url http://192.168.123.56:8080 --show-preview

# 限制帧率到 5fps（减轻 CPU 负担）
python pc_simulator.py --fps 5
```

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--broker` | `localhost:1883` | MQTT Broker 地址 |
| `--device-id` | `pc-sim-001` | 设备标识 |
| `--server-url` | `None` | 后端 API 地址（可选） |
| `--fps` | `10` | 帧率限制 |
| `--show-preview` | `False` | 显示实时画面窗口 |
| `--camera` | `0` | 摄像头索引 |

## 数据格式

### MQTT 主题

| 主题 | 方向 | 说明 |
|------|------|------|
| `fitness/<device_id>/pose` | PC → 服务器 | 姿态数据（每帧） |
| `fitness/<device_id>/status` | PC → 服务器 | 心跳（每 30s） |

### Pose 消息格式

```json
{
  "device_id": "pc-sim-001",
  "session_id": "gen-xxxxx",
  "timestamp": 1715000000.123,
  "frame_number": 42,
  "keypoints": [{"id": 0, "x": 0.52, "y": 0.31, "z": -0.05, "visibility": 0.98}, ...],
  "angles": {
    "left_knee": 95.2, "right_knee": 98.7,
    "left_hip": 78.3, "right_hip": 80.1,
    "left_elbow": 165.4, "right_elbow": 168.2
  },
  "exercise_type": "squat",
  "reps": 5,
  "form_score": 85.5
}
```

## 与 STM32 的关系

此模拟器的 MQTT 数据格式与 `edge_device/` 目录下的 STM32 方案保持一致。未来将 STM32 接入时，后端无需修改代码，只要按同样的格式发布到 `fitness/<device_id>/pose` 即可。

## 依赖

- opencv-python — 摄像头采集 + 预览
- numpy — 角度计算
- paho-mqtt — MQTT 发布
- mediapipe — 姿态估计（复用 `ai_vision/pose_engine.py`）
