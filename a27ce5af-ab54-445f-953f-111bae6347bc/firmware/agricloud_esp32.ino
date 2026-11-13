/*
 * AgriCloud-CI - ESP32 edge node firmware
 * --------------------------------------
 * Reads a capacitive soil moisture probe and a DHT11, averages five samples,
 * and posts the result to the AgriCloud-CI backend every 5 minutes over HTTPS.
 *
 * Required libraries (Arduino IDE -> Tools -> Manage Libraries):
 *   - "DHT sensor library" by Adafruit
 *   - "Adafruit Unified Sensor" (dependency of the DHT library)
 *
 * Board: "ESP32 Dev Module" (Arduino-ESP32 core 2.x or newer)
 *
 * Wiring
 *   Capacitive soil sensor  VCC -> 3V3, GND -> GND, AOUT -> GPIO 34 (ADC1)
 *   DHT11                   VCC -> 3V3, GND -> GND, DATA -> GPIO 4
 *   DHT11 DATA needs a 10 kΩ pull-up to 3V3 and a 100 nF decoupling capacitor
 *   between VCC and GND close to the sensor, as described in the synopsis.
 *
 * Calibration
 *   1. Hold the probe in dry air, open the Serial Monitor and note the raw ADC
 *      value -> that is DRY_VALUE (around 3200 on a 12-bit ESP32 ADC).
 *   2. Hold the probe in a glass of water (up to the line marked on the PCB)
 *      and note the raw value -> that is WET_VALUE (around 1300).
 *   Never submerge the electronics above the marked line.
 */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <DHT.h>

// ===========================================================================
// FILL THESE IN before flashing. They are intentionally empty in the repo so
// no credential is ever committed; keep your copy out of version control.
// ===========================================================================
const char *WIFI_SSID = "";                          // fill these in
const char *WIFI_PASSWORD = "";                      // fill these in
const char *API_KEY = "";                            // fill these in (must match DEVICE_API_KEY)
const char *SERVER_HOST = "your-project.vercel.app"; // fill these in (host only, no https://)
const uint16_t SERVER_PORT = 443;
const char *TELEMETRY_PATH = "/api/telemetry";

// ===========================================================================
// Hardware configuration
// ===========================================================================
const int SOIL_PIN = 34;  // ADC1 channel, input-only pin (safe for analog reads)
const int DHT_PIN = 4;    // digital GPIO for the DHT11 data line
#define DHT_TYPE DHT11

// Calibration constants: raw ADC value in dry air and in water.
const int DRY_VALUE = 3200;
const int WET_VALUE = 1300;

// Sampling and upload schedule.
const int SAMPLE_COUNT = 5;                        // readings averaged per cycle
const unsigned long SAMPLE_GAP_MS = 1200;          // DHT11 needs ~1 s between reads
const unsigned long SEND_INTERVAL_MS = 5UL * 60UL * 1000UL;  // every 5 minutes
const unsigned long WIFI_CONNECT_TIMEOUT_MS = 20000;

// TLS: for a college demo we skip certificate validation because the Vercel
// certificate chain changes. In a production field deployment, set this to
// false and install the root CA with client.setCACert(root_ca).
const bool TLS_INSECURE = true;

DHT dht(DHT_PIN, DHT_TYPE);
unsigned long lastSendAt = 0;  // millis() of the last upload attempt

// ---------------------------------------------------------------------------
// Wi-Fi
// ---------------------------------------------------------------------------
bool connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return true;
  }

  Serial.printf("[wifi] connecting to \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  const unsigned long startedAt = millis();
  while (WiFi.status() != WL_CONNECTED &&
         millis() - startedAt < WIFI_CONNECT_TIMEOUT_MS) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[wifi] connected, IP address: ");
    Serial.println(WiFi.localIP());
    return true;
  }

  Serial.println("[wifi] connection timed out");
  return false;
}

// ---------------------------------------------------------------------------
// Sensors
// ---------------------------------------------------------------------------
float readSoilMoisturePct() {
  const int raw = analogRead(SOIL_PIN);
  // Linear mapping: DRY_VALUE -> 0 %, WET_VALUE -> 100 %.
  float percentage = (float)(DRY_VALUE - raw) * 100.0f / (float)(DRY_VALUE - WET_VALUE);
  if (percentage < 0.0f) percentage = 0.0f;
  if (percentage > 100.0f) percentage = 100.0f;
  return percentage;
}

/**
 * Take SAMPLE_COUNT readings and average them.
 * Returns false when the DHT11 produced no usable sample in this cycle.
 */
bool readAveraged(float &temperatureOut, float &humidityOut, float &soilOut) {
  float temperatureSum = 0.0f;
  float humiditySum = 0.0f;
  float soilSum = 0.0f;
  int validDhtSamples = 0;

  for (int i = 0; i < SAMPLE_COUNT; i++) {
    const float temperature = dht.readTemperature();
    const float humidity = dht.readHumidity();
    soilSum += readSoilMoisturePct();

    if (!isnan(temperature) && !isnan(humidity)) {
      temperatureSum += temperature;
      humiditySum += humidity;
      validDhtSamples++;
    } else {
      Serial.printf("[sensors] DHT11 read %d failed, skipping that sample\n", i + 1);
    }

    if (i < SAMPLE_COUNT - 1) {
      delay(SAMPLE_GAP_MS);
    }
  }

  if (validDhtSamples == 0) {
    return false;
  }

  temperatureOut = temperatureSum / validDhtSamples;
  humidityOut = humiditySum / validDhtSamples;
  soilOut = soilSum / SAMPLE_COUNT;
  return true;
}

// ---------------------------------------------------------------------------
// HTTPS upload
// ---------------------------------------------------------------------------
bool postReading(float temperature, float humidity, float soilMoisture) {
  WiFiClientSecure client;
  client.setTimeout(10);  // seconds for the TLS handshake and reads

  if (TLS_INSECURE) {
    client.setInsecure();
  }

  if (!client.connect(SERVER_HOST, SERVER_PORT)) {
    Serial.println("[upload] TLS connection failed");
    return false;
  }

  // Values are already constrained to the server's accepted ranges.
  String body = "{\"temp\":" + String(temperature, 1) +
                ",\"humidity\":" + String(humidity, 1) +
                ",\"soil_moisture\":" + String(soilMoisture, 1) + "}";

  client.print(String("POST ") + TELEMETRY_PATH + " HTTP/1.1\r\n");
  client.print(String("Host: ") + SERVER_HOST + "\r\n");
  client.print("Content-Type: application/json\r\n");
  client.print(String("X-API-Key: ") + API_KEY + "\r\n");
  client.print(String("Content-Length: ") + body.length() + "\r\n");
  client.print("Connection: close\r\n\r\n");
  client.print(body);

  // Read the status line, e.g. "HTTP/1.1 201 Created".
  const String statusLine = client.readStringUntil('\n');
  int statusCode = -1;
  const int firstSpace = statusLine.indexOf(' ');
  if (firstSpace > 0) {
    statusCode = statusLine.substring(firstSpace + 1).toInt();
  }

  client.stop();

  if (statusCode == 201 || statusCode == 200) {
    Serial.printf("[upload] %d OK - payload %s\n", statusCode, body.c_str());
    return true;
  }

  Serial.printf("[upload] server replied %d (%s)\n", statusCode, statusLine.c_str());
  return false;
}

// ---------------------------------------------------------------------------
// One sense + upload cycle
// ---------------------------------------------------------------------------
void runCycle() {
  float temperature = 0.0f;
  float humidity = 0.0f;
  float soilMoisture = 0.0f;

  // Sensing happens even when the network is down, so the node keeps working
  // in the field and retries the upload on the next cycle.
  if (!readAveraged(temperature, humidity, soilMoisture)) {
    Serial.println("[cycle] no valid DHT11 samples, skipping this cycle");
    return;
  }

  Serial.printf("[sensors] temp=%.1f C humidity=%.1f %% soil=%.1f %%\n",
                temperature, humidity, soilMoisture);

  if (!connectWiFi()) {
    Serial.println("[cycle] Wi-Fi down - upload skipped, retrying next cycle");
    return;
  }

  if (!postReading(temperature, humidity, soilMoisture)) {
    Serial.println("[cycle] upload failed - retrying next cycle");
  }
}

// ---------------------------------------------------------------------------
// Arduino entry points
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("AgriCloud-CI edge node starting");

  pinMode(SOIL_PIN, INPUT);
  analogReadResolution(12);      // 0..4095
  analogSetAttenuation(ADC_11db); // full 0..3.3 V input range
  dht.begin();

  connectWiFi();  // best effort; loop() retries when it fails

  // Send one reading right away so the dashboard fills in immediately.
  lastSendAt = millis() - SEND_INTERVAL_MS;
}

void loop() {
  if (millis() - lastSendAt >= SEND_INTERVAL_MS) {
    lastSendAt = millis();
    runCycle();
  }
  delay(1000);
}
