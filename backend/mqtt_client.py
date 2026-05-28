"""
mqtt_client.py - MQTT Client Handler
=====================================
Framework: paho-mqtt
  Source: https://github.com/eclipse/paho.mqtt.python (EPL 2.0 / EDPL)
  
Handles MQTT communication with ESP32 edge devices.
Subscribes to device topics and publishes commands.

Deployment:
  pip install paho-mqtt

MQTT Broker:
  - Local: mosquitto (apt install mosquitto / brew install mosquitto)
  - Docker: docker run -p 1883:1883 eclipse-mosquitto
  - Cloud: HiveMQ / EMQX (for production)
"""

import json
import time
import logging
import threading
from typing import Callable, Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MQTTClientHandler:
    """
    MQTT client that bridges ESP32 sensor data to the backend API.

    Default topics:
      fitness/<device_id>/sensor   - Sensor readings from device
      fitness/<device_id>/status   - Device heartbeat status
      fitness/<device_id>/command  - Commands TO device
      fitness/<device_id>/feedback - Feedback messages TO device
    """

    def __init__(self,
                 broker_host: str = "localhost",
                 broker_port: int = 1883,
                 client_id: str = "fitness-backend",
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 use_tls: bool = False):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.client_id = client_id
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self._connected = False
        self._callbacks: dict = {}
        self.pose_callback = None

        # Initialize MQTT client
        self.client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv311
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        if username and password:
            self.client.username_pw_set(username, password)

        if use_tls:
            self.client.tls_set()

        # Auto-reconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

    def connect(self):
        """Connect to MQTT broker."""
        try:
            self.client.connect(self.broker_host, self.broker_port, keepalive=60)
            logger.info(f"[MQTT] Connected to {self.broker_host}:{self.broker_port}")
            self._connected = True
        except Exception as e:
            logger.error(f"[MQTT] Connection failed: {e}")

    def disconnect(self):
        """Disconnect from MQTT broker."""
        self.client.disconnect()
        self._connected = False
        logger.info("[MQTT] Disconnected")

    def loop_forever(self):
        """Blocking MQTT event loop (runs in separate thread)."""
        self.client.loop_forever()

    def loop_start(self):
        """Start MQTT event loop in background thread."""
        self.client.loop_start()

    def loop_stop(self):
        """Stop MQTT event loop."""
        self.client.loop_stop()

    def is_connected(self) -> bool:
        return self._connected

    def subscribe(self, topic: str, callback: Optional[Callable] = None):
        """Subscribe to a topic and optionally register a callback."""
        self.client.subscribe(topic)
        if callback:
            self._callbacks[topic] = callback
        logger.info(f"[MQTT] Subscribed to {topic}")

    def subscribe_device(self, device_id: str):
        """Subscribe to all topics for a specific device."""
        self.subscribe(f"fitness/{device_id}/sensor")
        self.subscribe(f"fitness/{device_id}/status")

    def publish(self, topic: str, payload: dict, qos: int = 1):
        """Publish a JSON message to a topic."""
        message = json.dumps(payload)
        result = self.client.publish(topic, message, qos=qos)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.debug(f"[MQTT] Published to {topic}: {len(message)} bytes")
        else:
            logger.warning(f"[MQTT] Publish failed to {topic}: rc={result.rc}")
        return result

    def send_command(self, device_id: str, command: str, params: Optional[dict] = None):
        """Send a command to an edge device."""
        payload = {
            "command": command,
            "params": params or {},
            "timestamp": time.time()
        }
        self.publish(f"fitness/{device_id}/command", payload)

    def set_pose_callback(self, cb):
        """注册姿态数据回调（供 main.py 调用）"""
        self.pose_callback = cb

    def send_feedback(self, device_id: str, severity: str, message: str):
        """Send form feedback to an edge device."""
        payload = {
            "severity": severity,
            "message": message,
            "timestamp": time.time()
        }
        self.publish(f"fitness/{device_id}/feedback", payload)

    def _on_connect(self, client, userdata, flags, rc):
        """Callback when MQTT connection is established."""
        if rc == 0:
            self._connected = True
            logger.info(f"[MQTT] Connected OK (rc={rc})")

            # Subscribe to all device topics
            self.subscribe("fitness/+/sensor")
            self.subscribe("fitness/+/status")
            self.subscribe("fitness/+/pose")
        else:
            self._connected = False
            logger.error(f"[MQTT] Connection failed (rc={rc})")

    def _on_disconnect(self, client, userdata, rc):
        """Callback on MQTT disconnect."""
        self._connected = False
        if rc != 0:
            logger.info(f"[MQTT] Disconnected (rc={rc}), auto-reconnect enabled")

    def _on_message(self, client, userdata, msg):
        """Callback for incoming MQTT messages."""
        try:
            payload = json.loads(msg.payload.decode())
            topic = msg.topic
            logger.debug(f"[MQTT] Received on {topic}")

            # Check for registered callback
            if topic in self._callbacks:
                self._callbacks[topic](payload)

            # Generic processing based on topic pattern
            if topic.endswith("/pose") and self.pose_callback:
                self.pose_callback(payload)
            elif topic.endswith("/sensor"):
                self._process_sensor_data(payload)
            elif topic.endswith("/status"):
                self._process_device_status(payload)

        except json.JSONDecodeError:
            logger.warning(f"[MQTT] Invalid JSON on {msg.topic}")
        except Exception as e:
            logger.error(f"[MQTT] Message handler error: {e}")

    def _process_sensor_data(self, data: dict):
        """Process incoming sensor readings."""
        device_id = data.get("device_id", "unknown")
        hr = data.get("hr_bpm", 0)
        movement = data.get("movement", 0)

        if hr > 0:
            logger.info(f"[Sensor] {device_id}: HR={hr}bpm, Movement={movement:.2f}")

        # Forward to WebSocket if active
        # This would integrate with main.py's ws_manager in production

    def _process_device_status(self, data: dict):
        """Process device status heartbeat."""
        device_id = data.get("device_id", "unknown")
        rssi = data.get("rssi", 0)
        uptime = data.get("uptime_s", 0)
        logger.info(f"[Status] {device_id}: RSSI={rssi}dBm, Uptime={uptime}s")


# ---------- Test/Example ----------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    handler = MQTTClientHandler()
    handler.connect()
    handler.loop_start()

    print("[MQTT] Test client running. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        handler.loop_stop()
        handler.disconnect()
