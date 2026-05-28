/*
 * camera_probe.ino - 摄像头识别诊断
 *
 * 目的：用 10MHz XCLK + 极宽松配置先把 SCCB 通讯打通，
 * 然后通过 sensor_t->id 读出 PID / MIDH / MIDL，告诉我们到底是什么芯片。
 *
 * 用法：
 *   1) Arduino IDE 选板 AI Thinker ESP32-CAM
 *   2) IO0 接 GND 烧录，烧完拔 IO0 按 RST
 *   3) 串口 115200 看 [PROBE] PID=0xXX...
 *
 * 已知 PID：
 *   0x26 -> OV2640
 *   0x3A -> OV3660 (有的库写 0x363A)
 *   0x56 -> OV5640
 *   0x77 -> OV7725
 *   0x99 -> OV9650
 */

#include <esp_camera.h>

// AI-Thinker ESP32-CAM 引脚
#define PWDN_GPIO_NUM    32
#define RESET_GPIO_NUM   -1
#define XCLK_GPIO_NUM     0
#define SIOD_GPIO_NUM    26
#define SIOC_GPIO_NUM    27
#define Y9_GPIO_NUM      35
#define Y8_GPIO_NUM      34
#define Y7_GPIO_NUM      39
#define Y6_GPIO_NUM      36
#define Y5_GPIO_NUM      21
#define Y4_GPIO_NUM      19
#define Y3_GPIO_NUM      18
#define Y2_GPIO_NUM       5
#define VSYNC_GPIO_NUM   25
#define HREF_GPIO_NUM    23
#define PCLK_GPIO_NUM    22

bool tryInit(uint32_t xclk, framesize_t fs, int q) {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer   = LEDC_TIMER_0;
  c.pin_d0       = Y2_GPIO_NUM;
  c.pin_d1       = Y3_GPIO_NUM;
  c.pin_d2       = Y4_GPIO_NUM;
  c.pin_d3       = Y5_GPIO_NUM;
  c.pin_d4       = Y6_GPIO_NUM;
  c.pin_d5       = Y7_GPIO_NUM;
  c.pin_d6       = Y8_GPIO_NUM;
  c.pin_d7       = Y9_GPIO_NUM;
  c.pin_xclk     = XCLK_GPIO_NUM;
  c.pin_pclk     = PCLK_GPIO_NUM;
  c.pin_vsync    = VSYNC_GPIO_NUM;
  c.pin_href     = HREF_GPIO_NUM;
  c.pin_sscb_sda = SIOD_GPIO_NUM;
  c.pin_sscb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn     = PWDN_GPIO_NUM;
  c.pin_reset    = RESET_GPIO_NUM;
  c.xclk_freq_hz = xclk;
  c.pixel_format = PIXFORMAT_JPEG;
  c.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;
  c.fb_location  = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  c.frame_size   = fs;
  c.jpeg_quality = q;
  c.fb_count     = 1;

  esp_err_t err = esp_camera_init(&c);
  Serial.printf("[TRY] xclk=%lu fs=%d q=%d -> 0x%x\n",
                (unsigned long)xclk, (int)fs, q, err);
  if (err == ESP_OK) return true;
  esp_camera_deinit();
  return false;
}

void dumpSensor() {
  sensor_t* s = esp_camera_sensor_get();
  if (!s) {
    Serial.println("[PROBE] sensor_t = NULL");
    return;
  }
  Serial.printf("[PROBE] PID  = 0x%02x\n", s->id.PID);
  Serial.printf("[PROBE] VER  = 0x%02x\n", s->id.VER);
  Serial.printf("[PROBE] MIDH = 0x%02x\n", s->id.MIDH);
  Serial.printf("[PROBE] MIDL = 0x%02x\n", s->id.MIDL);

  const char* name = "UNKNOWN";
  switch (s->id.PID) {
    case OV2640_PID: name = "OV2640"; break;
    case OV3660_PID: name = "OV3660"; break;
    case OV5640_PID: name = "OV5640"; break;
    case OV7725_PID: name = "OV7725"; break;
    case OV7670_PID: name = "OV7670"; break;
#ifdef OV9650_PID
    case OV9650_PID: name = "OV9650"; break;
#endif
    default: break;
  }
  Serial.printf("[PROBE] => %s\n", name);

  // 抓一帧确认数据通路
  camera_fb_t* fb = esp_camera_fb_get();
  if (fb) {
    Serial.printf("[PROBE] frame ok, %dx%d, %u bytes\n",
                  fb->width, fb->height, (unsigned)fb->len);
    esp_camera_fb_return(fb);
  } else {
    Serial.println("[PROBE] frame FAIL");
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("=== ESP32-CAM Sensor Probe ===");
  Serial.printf("PSRAM: %s\n", psramFound() ? "YES" : "NO");

  // 顺序：10MHz QQVGA -> 10MHz QVGA -> 20MHz QVGA
  bool ok = false;
  if (tryInit(10000000, FRAMESIZE_QQVGA, 12)) ok = true;
  else if (tryInit(10000000, FRAMESIZE_QVGA, 12)) ok = true;
  else if (tryInit(20000000, FRAMESIZE_QVGA, 12)) ok = true;
  else if (tryInit( 8000000, FRAMESIZE_QQVGA, 15)) ok = true;

  if (!ok) {
    Serial.println("[PROBE] all init attempts failed.");
    Serial.println("        -> 排线/电源/摄像头模块本身问题。");
    return;
  }

  dumpSensor();
}

void loop() {
  delay(1000);
}
