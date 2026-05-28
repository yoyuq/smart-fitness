/*
 * camera_test.ino - 摄像头型号诊断工具
 * 会尝试所有已知的摄像头型号，看哪个能认出来
 */

#include <esp_camera.h>

// AI-Thinker ESP32-CAM 引脚
#define PWDN_GPIO_NUM    -1
#define RESET_GPIO_NUM   -1
#define XCLK_GPIO_NUM     4
#define SIOD_GPIO_NUM     5
#define SIOC_GPIO_NUM    18
#define Y9_GPIO_NUM      19
#define Y8_GPIO_NUM      36
#define Y7_GPIO_NUM      39
#define Y6_GPIO_NUM      34
#define Y5_GPIO_NUM      35
#define Y4_GPIO_NUM      14
#define Y3_GPIO_NUM      13
#define Y2_GPIO_NUM      15
#define VSYNC_GPIO_NUM   25
#define HREF_GPIO_NUM    23
#define PCLK_GPIO_NUM    22

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== Camera Module Test ===");
  Serial.printf("PSRAM found: %s\n", psramFound() ? "YES" : "NO");

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location  = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  config.frame_size   = FRAMESIZE_QVGA;
  config.jpeg_quality = 12;
  config.fb_count     = 1;

  // 先尝试 OV2640 模式
  Serial.println("\n--- Try 1: OV2640 (standard, QVGA) ---");
  config.frame_size = FRAMESIZE_QVGA;
  esp_err_t err = esp_camera_init(&config);
  if (err == ESP_OK) {
    sensor_t* s = esp_camera_sensor_get();
    if (s) {
      Serial.printf("Sensor PID: 0x%x\n", s->id.PID);
      if (s->id.PID == OV2640_PID) Serial.println("=> OV2640");
      else if (s->id.PID == OV3660_PID) Serial.println("=> OV3660");
      else if (s->id.PID == OV5640_PID) Serial.println("=> OV5640");
      else if (s->id.PID == OV7670_PID) Serial.println("=> OV7670");
      else Serial.println("=> Unknown sensor");
    }
    Serial.println("[OK] Camera works at QVGA!");
    esp_camera_deinit();
  } else {
    Serial.printf("[FAIL] 0x%x\n", err);
  }

  // 尝试 UXGA（如果 PSRAM 存在）
  if (psramFound()) {
    Serial.println("\n--- Try 2: UXGA (with PSRAM) ---");
    config.frame_size = FRAMESIZE_UXGA;
    config.jpeg_quality = 10;
    config.fb_count = 2;
    config.grab_mode = CAMERA_GRAB_LATEST;
    
    err = esp_camera_init(&config);
    if (err == ESP_OK) {
      sensor_t* s = esp_camera_sensor_get();
      if (s) {
        Serial.printf("Sensor PID: 0x%x\n", s->id.PID);
        if (s->id.PID == OV2640_PID) Serial.println("=> OV2640");
        else if (s->id.PID == OV3660_PID) Serial.println("=> OV3660");
        else if (s->id.PID == OV5640_PID) Serial.println("=> OV5640");
        else Serial.println("=> Unknown sensor");
      }
      Serial.println("[OK] Camera works at UXGA!");
      esp_camera_deinit();
    } else {
      Serial.printf("[FAIL] 0x%x\n", err);
    }
  }

  // 尝试 VGA
  Serial.println("\n--- Try 3: VGA (no PSRAM fallback) ---");
  config.frame_size = FRAMESIZE_VGA;
  config.jpeg_quality = 15;
  config.fb_count = 1;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_DRAM;
  
  err = esp_camera_init(&config);
  if (err == ESP_OK) {
    sensor_t* s = esp_camera_sensor_get();
    if (s) {
      Serial.printf("Sensor PID: 0x%x\n", s->id.PID);
      if (s->id.PID == OV2640_PID) Serial.println("=> OV2640");
      else if (s->id.PID == OV3660_PID) Serial.println("=> OV3660");
      else Serial.println("=> Unknown/other");
    }
    Serial.println("[OK] Camera works at VGA!");
    esp_camera_deinit();
  } else {
    Serial.printf("[FAIL] 0x%x\n", err);
  }

  // 尝试 SVGA
  Serial.println("\n--- Try 4: SVGA (800x600) ---");
  config.frame_size = FRAMESIZE_SVGA;
  config.jpeg_quality = 15;
  
  err = esp_camera_init(&config);
  if (err == ESP_OK) {
    sensor_t* s = esp_camera_sensor_get();
    if (s) {
      Serial.printf("Sensor PID: 0x%x\n", s->id.PID);
    }
    Serial.println("[OK] Camera works at SVGA!");
    esp_camera_deinit();
  } else {
    Serial.printf("[FAIL] 0x%x\n", err);
  }

  // 尝试 CIF (低分辨率兼容模式)
  Serial.println("\n--- Try 5: CIF (low-res compatibility) ---");
  config.frame_size = FRAMESIZE_CIF;
  config.jpeg_quality = 20;
  
  err = esp_camera_init(&config);
  if (err == ESP_OK) {
    sensor_t* s = esp_camera_sensor_get();
    if (s) {
      Serial.printf("Sensor PID: 0x%x\n", s->id.PID);
    }
    Serial.println("[OK] Camera works at CIF!");
    esp_camera_deinit();
  } else {
    Serial.printf("[FAIL] 0x%x\n", err);
  }

  Serial.println("\n=== All tests done ===");
  Serial.println("If all failed: check ribbon cable, power, or defective camera module.");
}

void loop() {
  delay(10000);
}
