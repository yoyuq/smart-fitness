/*
 * esp32cam_fitness.ino  (v3 - Dual Channel)
 * =========================================
 * 双通道架构:
 *   通道1 (port 81): MJPEG stream server, APP 直连看 10fps 实时画面
 *   通道2 (port 8080): HTTP POST /api/v2/vision/infer/full, 后端跑推理
 *
 * 训练态 (后端 active_trainings[device_id]) 决定推理通道是否上传:
 *   - 训练中: 后端返回 next_interval_ms=500, ESP32 按 2fps 推理
 *   - 未训练: 后端返回 next_interval_ms=10000 + paused=true, ESP32 几乎不上传
 *   - 但 MJPEG stream 始终运行, APP 永远能看到画面
 *
 * 关键: esp_camera fb_get/fb_return 是线程安全的 (有内部 mutex),
 *       两个 task 可以同时 grab frame, 互不阻塞.
 *
 * 串口波特率: 115200
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <WebServer.h>
#include <ArduinoOTA.h>
#include <esp_camera.h>
#include <esp_http_server.h>
#include <base64.h>

// =====================================================
// 配置
// =====================================================
static const char* WIFI_SSID   = "OPPO";
static const char* WIFI_PASS   = "87654321";

static const char* SERVER_HOST = "192.168.72.56";
static const int   SERVER_PORT = 8080;
static const char* API_PATH    = "/api/v2/vision/infer/full";

static const char* DEVICE_ID    = "esp32cam-001";
static const char* DEVICE_TOKEN = "aabb35";

#define ENABLE_OTA          1
static const char* OTA_HOSTNAME = "esp32cam-fitness";
static const char* OTA_PASSWORD = "fit2026";

// MJPEG stream server 端口
#define STREAM_PORT 81

// =====================================================
// 引脚 (AI-Thinker)
// =====================================================
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

#define LED_PIN          33
#define LED_ON()  digitalWrite(LED_PIN, LOW)
#define LED_OFF() digitalWrite(LED_PIN, HIGH)

// =====================================================
// 参数
// =====================================================
#define DEFAULT_INTERVAL_MS  10000   // 默认: 不训练时 10s/帧
#define MIN_INTERVAL_MS        300
#define MAX_INTERVAL_MS      30000

#define MAX_FRAME_BYTES      40000
#define MIN_FRAME_BYTES       8000

#define HTTP_TIMEOUT_MS       5000
#define HTTP_MAX_RETRIES         2

// =====================================================
// 全局
// =====================================================
static WiFiClient client;
static unsigned long last_capture_ms = 0;
static unsigned long current_interval_ms = DEFAULT_INTERVAL_MS;
static int  current_quality = 12;
static int  capture_count = 0;
static int  success_count = 0;
static int  failure_count = 0;
static bool training_active = false;  // 由 server hint paused 字段反推

// MJPEG stream server handle (HTTP)
static httpd_handle_t stream_httpd = NULL;
static long stream_frame_count = 0;

// 前置声明
bool   initCamera();
bool   connectWiFi();
void   ledBlink(int times, int onMs = 80, int offMs = 80);
bool   captureLoop();
String httpPostInfer(const String& body, int* statusOut);
void   parseAndApplyServerHints(const String& json);
void   startMjpegServer();
esp_err_t streamHandler(httpd_req_t* req);

// =====================================================
// MJPEG Stream Handler (HTTP port 81)
// APP 访问 http://esp32-ip:81/stream 拉流
// =====================================================
#define PART_BOUNDARY "123456789000000000000987654321"
static const char* _STREAM_CONTENT_TYPE =
    "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* _STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* _STREAM_PART =
    "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

esp_err_t streamHandler(httpd_req_t* req) {
    esp_err_t res = ESP_OK;
    char partBuf[64];

    res = httpd_resp_set_type(req, _STREAM_CONTENT_TYPE);
    if (res != ESP_OK) return res;
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "X-Framerate", "10");

    while (true) {
        camera_fb_t* fb = esp_camera_fb_get();
        if (!fb) {
            Serial.println("[STREAM] capture failed");
            res = ESP_FAIL;
            break;
        }
        stream_frame_count++;

        // boundary
        if (res == ESP_OK) res = httpd_resp_send_chunk(req, _STREAM_BOUNDARY, strlen(_STREAM_BOUNDARY));
        // header
        size_t hlen = snprintf(partBuf, 64, _STREAM_PART, fb->len);
        if (res == ESP_OK) res = httpd_resp_send_chunk(req, partBuf, hlen);
        // JPEG body
        if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);

        esp_camera_fb_return(fb);

        if (res != ESP_OK) break;

        // ~10fps → 100ms/frame
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    return res;
}

void startMjpegServer() {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = STREAM_PORT;
    config.ctrl_port = 32768;
    config.max_uri_handlers = 1;
    config.stack_size = 4096;

    if (httpd_start(&stream_httpd, &config) == ESP_OK) {
        httpd_uri_t uri = {
            .uri       = "/stream",
            .method    = HTTP_GET,
            .handler   = streamHandler,
            .user_ctx  = NULL
        };
        httpd_register_uri_handler(stream_httpd, &uri);
        Serial.printf("[STREAM] MJPEG server started on port %d\n", STREAM_PORT);
    } else {
        Serial.println("[STREAM] FAILED to start MJPEG server");
    }
}

// =====================================================
// setup()
// =====================================================
void setup() {
    Serial.begin(115200);
    delay(500);

    pinMode(LED_PIN, OUTPUT);
    LED_OFF();

    Serial.println();
    Serial.println("================================================");
    Serial.println(" Smart Fitness ESP32-CAM v3 (Dual Channel)");
    Serial.println("================================================");
    Serial.printf("Device:       %s\n", DEVICE_ID);
    Serial.printf("Infer server: http://%s:%d%s\n", SERVER_HOST, SERVER_PORT, API_PATH);
    Serial.printf("MJPEG stream: http://[esp32-ip]:%d/stream\n", STREAM_PORT);
    Serial.printf("Heap free:    %u  PSRAM: %s\n", ESP.getFreeHeap(), psramFound() ? "yes" : "no");
    Serial.println("------------------------------------------------");

    if (!initCamera()) {
        Serial.println("[FATAL] camera init failed");
        ledBlink(5, 200, 200);
        delay(3000); ESP.restart();
    }
    if (!connectWiFi()) {
        Serial.println("[FATAL] WiFi failed");
        ledBlink(5, 200, 200);
        delay(3000); ESP.restart();
    }

    // 启动 MJPEG stream server (port 81)
    startMjpegServer();

#if ENABLE_OTA
    ArduinoOTA.setHostname(OTA_HOSTNAME);
    ArduinoOTA.setPassword(OTA_PASSWORD);
    ArduinoOTA.onStart([]() { Serial.println("[OTA] starting..."); LED_ON(); });
    ArduinoOTA.onEnd([]() { Serial.println("[OTA] done."); LED_OFF(); });
    ArduinoOTA.onProgress([](unsigned int p, unsigned int t) {
        if ((p % (t / 10 + 1)) == 0) Serial.printf("[OTA] %u%%\n", (p * 100) / t);
    });
    ArduinoOTA.onError([](ota_error_t e) { Serial.printf("[OTA] err %u\n", e); ledBlink(5); });
    ArduinoOTA.begin();
    Serial.printf("[OTA] Ready, hostname=%s\n", OTA_HOSTNAME);
#endif

    ledBlink(2, 60, 60);
    Serial.println("[READY] Dual channel active");
}

// =====================================================
// loop() — 只管推理通道, MJPEG 在独立 httpd 线程跑
// =====================================================
void loop() {
#if ENABLE_OTA
    ArduinoOTA.handle();
#endif

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[WiFi] dropped, reconnect...");
        connectWiFi();
    }

    unsigned long now = millis();
    if (now - last_capture_ms >= current_interval_ms) {
        last_capture_ms = now;
        captureLoop();
    }

    delay(5);
}

// =====================================================
// initCamera()
// =====================================================
bool initCamera() {
    camera_config_t config = {};
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
    config.frame_size   = FRAMESIZE_QVGA;
    config.jpeg_quality = current_quality;
    // fb_count=2: 一个给 stream, 一个给 inference, 交替使用
    config.fb_count     = psramFound() ? 2 : 1;
    config.fb_location  = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("[CAM] init failed: 0x%x\n", err);
        return false;
    }

    sensor_t* s = esp_camera_sensor_get();
    if (s) {
        if (s->id.PID == OV3660_PID) {
            s->set_vflip(s, 1);
            s->set_brightness(s, 1);
            s->set_saturation(s, -2);
        }
        s->set_framesize(s, FRAMESIZE_QVGA);
    }

    Serial.printf("[CAM] init ok 320x240 JPEG q=%d\n", current_quality);
    return true;
}

// =====================================================
// connectWiFi()
// =====================================================
bool connectWiFi() {
    Serial.printf("[WiFi] connecting to %s ", WIFI_SSID);
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);

    for (int attempt = 1; attempt <= HTTP_MAX_RETRIES; attempt++) {
        WiFi.begin(WIFI_SSID, WIFI_PASS);
        unsigned long start = millis();
        while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
            delay(300); Serial.print(".");
        }
        if (WiFi.status() == WL_CONNECTED) {
            Serial.println();
            Serial.printf("[WiFi] IP=%s RSSI=%d\n",
                          WiFi.localIP().toString().c_str(), WiFi.RSSI());
            return true;
        }
        Serial.printf("\n[WiFi] attempt %d failed\n", attempt);
        WiFi.disconnect(true);
        delay(attempt * 2000);
    }
    return false;
}

// =====================================================
// ledBlink()
// =====================================================
void ledBlink(int times, int onMs, int offMs) {
    for (int i = 0; i < times; i++) {
        LED_ON();  delay(onMs);
        LED_OFF(); delay(offMs);
    }
}

// =====================================================
// captureLoop() — 推理通道
//   未训练时: current_interval_ms = 10000 (几乎不上传)
//   训练中:   current_interval_ms = 500   (2fps 推理)
//   训练停止: 后端返回 next_interval_ms=10000, 自动降频
// =====================================================
bool captureLoop() {
    capture_count++;

    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
        Serial.println("[CAM] capture failed");
        failure_count++;
        return false;
    }
    LED_ON(); delay(10); LED_OFF();

    // 帧大小守卫
    size_t len = fb->len;
    bool need_adj = false;
    if (len > MAX_FRAME_BYTES && current_quality < 25) {
        current_quality = min(25, current_quality + 3); need_adj = true;
    } else if (len < MIN_FRAME_BYTES && current_quality > 8) {
        current_quality = max(8, current_quality - 2); need_adj = true;
    }
    if (need_adj) {
        sensor_t* s = esp_camera_sensor_get();
        if (s) s->set_quality(s, current_quality);
    }

    Serial.printf("[#%d] frame=%uB q=%d training=%d\n",
                  capture_count, (unsigned)len, current_quality, training_active);

    // Base64 + POST
    String b64 = base64::encode(fb->buf, fb->len);
    esp_camera_fb_return(fb);

    String body = "{\"image\":\"";
    body += b64;
    body += "\",\"device_id\":\"";
    body += DEVICE_ID;
    body += "\"}";

    int status = 0;
    String resp = httpPostInfer(body, &status);

    if (status >= 200 && status < 300) {
        success_count++;
        ledBlink(2, 30, 30);
        Serial.printf("[HTTP] %d ok %dB\n", status, resp.length());
        parseAndApplyServerHints(resp);
    } else {
        failure_count++;
        ledBlink(3);
        Serial.printf("[HTTP] %d fail\n", status);
    }

    if (capture_count % 20 == 0) {
        Serial.printf("[STAT] cap=%d ok=%d fail=%d stream=%ld heap=%u\n",
                      capture_count, success_count, failure_count,
                      stream_frame_count, ESP.getFreeHeap());
    }
    return true;
}

// =====================================================
// httpPostInfer()
// =====================================================
String httpPostInfer(const String& body, int* statusOut) {
    *statusOut = 0;
    String last_response;

    for (int attempt = 1; attempt <= HTTP_MAX_RETRIES; attempt++) {
        if (!client.connect(SERVER_HOST, SERVER_PORT)) {
            Serial.printf("[HTTP] connect fail attempt=%d\n", attempt);
            delay(attempt * 300);
            continue;
        }

        client.printf("POST %s HTTP/1.1\r\n", API_PATH);
        client.printf("Host: %s:%d\r\n", SERVER_HOST, SERVER_PORT);
        client.println("Content-Type: application/json");
        client.printf("Content-Length: %d\r\n", body.length());
        client.printf("X-Device-Id: %s\r\n", DEVICE_ID);
        if (strlen(DEVICE_TOKEN) > 0)
            client.printf("X-Device-Token: %s\r\n", DEVICE_TOKEN);
        client.println("Connection: close");
        client.println();
        client.print(body);

        unsigned long start = millis();
        while (client.available() == 0) {
            if (millis() - start > HTTP_TIMEOUT_MS) {
                client.stop(); goto next_try;
            }
            delay(1);
        }

        // status line
        {
            String sl = client.readStringUntil('\n'); sl.trim();
            int s1 = sl.indexOf(' '), s2 = sl.indexOf(' ', s1 + 1);
            if (s1 > 0 && s2 > s1) *statusOut = sl.substring(s1 + 1, s2).toInt();
        }
        // skip headers
        while (client.connected()) {
            String line = client.readStringUntil('\n'); line.trim();
            if (line.length() == 0) break;
        }
        // body
        last_response = "";
        while (client.available()) last_response += (char)client.read();
        client.stop();

        if (*statusOut >= 200 && *statusOut < 300) return last_response;
        if (*statusOut == 401 || *statusOut == 403) return last_response;
next_try:
        delay(attempt * 400);
    }
    return last_response;
}

// =====================================================
// parseAndApplyServerHints()
//   从后端响应判断训练态:
//     paused=true  → 降频到 10s (未训练)
//     paused=false → 提频到 500ms (训练中)
//     next_interval_ms → 按服务器值
// =====================================================
void parseAndApplyServerHints(const String& json) {
    // 解析 paused
    bool paused = false;
    int pi = json.indexOf("\"paused\"");
    if (pi >= 0) {
        int c = json.indexOf(':', pi);
        String val = json.substring(c + 1, c + 6); val.trim();
        paused = val.startsWith("true");
    }
    training_active = !paused;

    // 解析 next_interval_ms
    int idx = json.indexOf("\"next_interval_ms\"");
    if (idx < 0) idx = json.indexOf("\"throttle_ms\"");
    if (idx < 0) {
        // 没有 interval hint, 用 paused 推断
        unsigned long target = paused ? 10000 : 500;
        if (current_interval_ms != target) {
            Serial.printf("[HINT] paused=%d → interval %lu→%lu\n", paused, current_interval_ms, target);
            current_interval_ms = target;
        }
        return;
    }
    int colon = json.indexOf(':', idx);
    long val = 0;
    int p = colon + 1;
    while (p < (int)json.length() && (json[p] == ' ' || json[p] == '"')) p++;
    while (p < (int)json.length() && isDigit(json[p])) { val = val * 10 + (json[p] - '0'); p++; }
    if (val >= MIN_INTERVAL_MS && val <= MAX_INTERVAL_MS) {
        if ((long)current_interval_ms != val) {
            Serial.printf("[HINT] interval %lu→%ld ms (server)\n", current_interval_ms, val);
            current_interval_ms = (unsigned long)val;
        }
    }
}
