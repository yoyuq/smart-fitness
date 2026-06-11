# 凭据轮换清单（2026-06-11）

## 背景

v1.0 提交（已推送到 GitHub `yoyuq/smart-fitness` 和 Gitee `a2078077334/smart-fitness` 公网仓库）的
`edge_esp32cam/esp32cam_fitness/esp32cam_fitness.ino` 中包含真实凭据。
源码已在本次提交中脱敏为占位符，但 **git 历史里仍然存在**，必须按下表轮换。

## 必须轮换的凭据

| 凭据 | 泄露位置 | 轮换方式 | 状态 |
|------|----------|----------|------|
| WiFi 密码 (热点 SSID 见历史) | v1.0 提交的 .ino | 路由器/手机热点上改密码 | ⬜ 需人工操作 |
| ESP32 DEVICE_TOKEN | v1.0 提交的 .ino | APP Profile 页重新 bind 生成新 token → 写入固件重新烧录；后端 devices 表删除旧 token | ⬜ 需重新烧录 |
| OTA 密码 | v1.0 提交的 .ino | 固件中改 `OTA_PASSWORD` 后重新烧录 | ⬜ 需重新烧录 |
| JWT_SECRET | 默认值 `smart-fitness-dev-secret-change-in-prod`（在公开源码中） | 已生成强随机值写入 `backend/.env`，`main.py` 已加载 dotenv | ✅ 已完成（重启后端生效，旧 JWT 全部失效需重新登录） |

## 注意事项

- `backend/.env` 已加入 `.gitignore`，不会入库。
- git 历史清洗（filter-repo + 双远程 force push）影响所有协作者的 clone，
  且凭据已轮换后历史中的旧值无害，**暂不执行**；若需要可后续单独做。
- ESP32 固件源码中现在是 `YOUR_WIFI_SSID` 等占位符，烧录前本地填写，不要提交真实值。
