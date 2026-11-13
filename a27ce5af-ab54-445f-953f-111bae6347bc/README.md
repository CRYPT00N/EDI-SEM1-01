# AgriCloud-CI

**Edge-to-cloud precision microclimate advisory platform via continuous delivery.**

An ESP32 node with a capacitive soil moisture probe and a DHT11 posts field
readings over HTTPS. The cloud backend combines them with Open-Meteo weather
data, decides whether conditions suit any of **eleven crops** (Wheat, Rice, Maize,
Cotton, Sugarcane, Groundnut, Mustard, Chickpea, Pearl Millet, Soybeans,
Tomato), stores everything in PostgreSQL and serves a three-page dashboard. If the device
goes offline or a probe reading looks impossible, the backend automatically
falls back to the Open-Meteo grid observation.

Built for the FF No. 180 project review (VIT, Department of DESH, Group 10).

---

## 1. Architecture

```
  Capacitive soil probe ─┐
                         ├─► ESP32 (5 samples, averaged) ──HTTPS POST──┐
  DHT11 (temp/humidity) ─┘                                            │
                                                                      ▼
                                              ┌──────────────────────────────────┐
                                              │  Vercel Python function          │
   Open-Meteo REST API ──────────────────────►│  api/index.py (FastAPI)          │
   (10 minute cache)                          │   1. authenticate X-API-Key      │
                                              │   2. fusion.py  (ESP32 vs grid)  │
                                              │   3. crops.json decision matrix  │
                                              │   4. write audit row             │
                                              └───────────────┬──────────────────┘
                                                              ▼
                                                   PostgreSQL (agri_telemetry_logs)
                                                              │
                                              ┌───────────────┴──────────────────┐
                                              │  Static dashboard (public/)      │
                                              │  Live · Advisory · History       │
                                              └──────────────────────────────────┘
```

Key design decisions (useful in the viva):

| Decision | Reason |
|---|---|
| Fusion happens **at read time**, not at ingestion | The spike rule needs the previous reading, and the 15 minute freshness rule is about "now", not about when the packet arrived. Uploads stay cheap and simple. |
| Weather is cached for 10 minutes in process | Open-Meteo is free and keyless, but every dashboard refresh must not hammer it. One call per cold instance per 10 minutes is enough. |
| Fallback soil moisture uses Open-Meteo `soil_moisture_0_to_7cm` | The device is the only ground-truth soil probe; the satellite path still needs a moisture number. The modelled value (m³/m³) is converted to percent, and a documented regional default is used if the model has no value. |
| PostgreSQL accessed with SQLAlchemy and `NullPool` | Serverless instances are short-lived; `NullPool` avoids holding connections open between invocations. |
| `created_at` uses a database `now()` default | Timestamps come from one clock (the database), not from the lambda. |

---

## 2. Repository layout

```
AgriCloud-CI/
├── api/                        # FastAPI backend (Vercel Python function)
│   ├── index.py                # App + all HTTP endpoints + static mount
│   ├── config.py               # Environment loading, defaults
│   ├── database.py             # SQLAlchemy model, engine, query helpers
│   ├── weather.py              # Open-Meteo client + 10 minute cache
│   ├── fusion.py               # Dual-source fusion and fallback rules
│   ├── decision_matrix.py      # Crop grading and recommendation text
│   ├── crops.json              # Editable agronomic rules (Wheat/Cotton/Soybeans)
│   ├── simulation.py           # SIMULATE=true synthetic readings
│   └── utils.py                # UTC / ISO timestamp helpers
├── public/                     # Dashboard, no build step
│   ├── index.html              # Live cards, source badge, 30 s auto-refresh
│   ├── advisory.html           # Crop dropdown + colour-coded verdict
│   ├── history.html            # Audit table, crop filter, date sorting
│   └── assets/
│       ├── app.js              # Shared fetch/badge/format helpers
│       └── app.css             # Small dark-theme extras
├── tests/                      # 103 pytest tests, no network or DB server needed
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_decision_matrix.py
│   ├── test_fusion.py
│   ├── test_simulation.py
│   └── test_weather.py
├── firmware/
│   └── agricloud_esp32.ino     # Arduino C++ edge node
├── .github/workflows/ci.yml    # flake8 + pytest + credentials, then deploy
├── .env.example                # Placeholders only
├── .flake8
├── .gitignore
├── docker-compose.yml          # Local PostgreSQL 16
├── pytest.ini
├── requirements.txt            # Runtime dependencies
├── requirements-dev.txt        # + pytest, flake8
├── run_checks.sh               # Pre-flight quality gate
├── vercel.json                 # Routes /api/* to api/index.py
└── README.md
```

---

## 3. Quick start (local)

Requirements: Python 3.11+, Docker Desktop (or any PostgreSQL 14+ server).

```bash
# 1. Configuration (placeholders only in the template)
cp .env.example .env
# open .env and set DEVICE_API_KEY, POSTGRES_PASSWORD and DATABASE_URL

# 2. Start PostgreSQL 16 in Docker
docker compose up -d
docker compose ps          # wait until the health status is "healthy"

# 3. Python environment
python -m venv .venv
source .venv/bin/activate              # Windows Git Bash: source .venv/Scripts/activate
pip install -r requirements-dev.txt

# 4. Run the API + dashboard
uvicorn index:app --app-dir api --reload --port 8000
```

Open <http://127.0.0.1:8000>. The table `agri_telemetry_logs` is created
automatically on the first request.

> Tip: set `SIMULATE=true` in `.env` to see the dashboard working without any
> hardware. No `.env`? You can also run with a local SQLite file for a quick
> demo: `DATABASE_URL=sqlite:///./demo.db`. PostgreSQL remains the production
> database used for the review.

### Send a reading by hand

```bash
curl -X POST http://127.0.0.1:8000/api/telemetry \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $DEVICE_API_KEY" \
  -d '{"temp": 26.1, "humidity": 58, "soil_moisture": 47.5}'
```

---

## 4. Environment variables

| Variable | Required | Default | Used by | Purpose |
|---|---|---|---|---|
| `DEVICE_API_KEY` | yes | — | API, firmware | Shared secret checked against the `X-API-Key` header. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `DATABASE_URL` | yes | — | API | PostgreSQL URL, e.g. `postgresql://user:password@host:5432/db`. `postgres://` is accepted and normalised. |
| `LATITUDE` | no | `18.5204` | API | Latitude sent to Open-Meteo. |
| `LONGITUDE` | no | `73.8567` | API | Longitude sent to Open-Meteo. |
| `SIMULATE` | no | `false` | API | `true` generates realistic synthetic ESP32 readings for demos. |
| `POSTGRES_DB` | compose only | `agricloud` | docker-compose | Local database name. |
| `POSTGRES_USER` | compose only | `agricloud` | docker-compose | Local database user. |
| `POSTGRES_PASSWORD` | compose only | — (required) | docker-compose | Local database password; deliberately has no default. |
| `POSTGRES_PORT` | no | `5432` | docker-compose | Host port for the container. |
| `FUSION_MAX_AGE_MINUTES` | no | `15` | API | Freshness limit before satellite fallback. |
| `SPIKE_THRESHOLD_PCT` | no | `40` | API | Soil moisture jump treated as a sensor spike. |
| `WEATHER_CACHE_SECONDS` | no | `600` | API | Open-Meteo cache lifetime (10 minutes). |

No key, password or database URL is ever written in the source. `run_checks.sh`
fails the build if one appears.

---

## 5. API reference

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/telemetry` | `X-API-Key` | Accepts `{temp, humidity, soil_moisture}`, validates ranges and stores the reading. |
| `GET` | `/api/live` | — | Latest fused reading, its source (`esp32` / `satellite_fallback`) and the fusion note. |
| `GET` | `/api/advisory?crop=Wheat` | — | Grades current conditions for any catalogue crop, saves the verdict and returns it. |
| `GET` | `/api/history?crop=&limit=50` | — | Audit rows newest first, optional crop filter. |
| `GET` | `/api/crops` | — | Crop catalogue with optimal/marginal bands; the dashboard builds its selector and crop library from this. |
| `GET` | `/api/health` | — | Readiness probe: simulation flag and whether a database is configured. |

Validation: temperature −20…60 °C, humidity 0…100 %, soil moisture 0…100 %.
Bad values return `422`; a missing or wrong key returns `401`; an unknown crop
returns `400`; if neither the device nor Open-Meteo can provide trustworthy data
the endpoint returns `503` with the reason.

Adding a crop means editing only `api/crops.json`: the dashboard reads
`GET /api/crops` at load time, so the dropdown, crop library and history filter
pick up the new entry automatically.

Example advisory response:

```json
{
  "log_id": 12,
  "crop": "Wheat",
  "viability": "Marginal",
  "recommendation": "Temperature is high for Wheat (measured 25.1 °C); heat above 30 °C shortens grain filling, so irrigate early in the morning and mulch.",
  "metric_status": {"temperature": "marginal_high", "humidity": "optimal", "soil_moisture": "optimal"},
  "conditions": {"temperature_c": 25.1, "humidity_pct": 58.3, "soil_moisture_pct": 46.4},
  "source": "esp32",
  "fusion_note": "Primary ESP32 reading accepted (age 0.0 min, all values within valid ranges).",
  "evaluated_at": "2026-11-12T04:48:50Z"
}
```

---

## 6. Dual-source fusion

`fusion.fuse()` is pure logic with no I/O, which is why every rule is unit
tested without a database or a network.

The ESP32 reading is used when **all** of these hold:

* it is at most 15 minutes old;
* temperature, humidity and soil moisture are inside the valid ranges;
* soil moisture did not move by more than 40 percentage points since the
  previous reading.

Otherwise the platform falls back to Open-Meteo, and `fusion_note` records every
reason, for example:

```
Satellite fallback (Open-Meteo): no ESP32 reading has arrived in the last 15
minutes. Grid observation from 2026-11-12T04:45:00+00:00.
```

```
Satellite fallback (Open-Meteo): soil moisture changed +45.0 percentage points
since the previous reading (sensor spike, limit 40).
```

The note is stored in PostgreSQL with the reading, so the review panel can see
*why* the cloud switched source.

---

## 7. Crop decision matrix

Rules live in [`api/crops.json`](api/crops.json) and cover eleven crops.
`decision_matrix.evaluate()` returns:

* **Optimal** – all three values inside their optimal band;
* **Marginal** – all three inside their marginal band, but not all optimal;
* **Unsuitable** – at least one value outside its marginal band.

Current bands (temperature °C, humidity %, soil moisture %):

| Crop | Temperature optimal / marginal | Humidity optimal / marginal | Soil moisture optimal / marginal |
|---|---|---|---|
| Wheat | 15–25 / 10–32 | 40–70 / 30–80 | 35–60 / 25–70 |
| Rice | 22–32 / 18–38 | 70–90 / 60–95 | 60–85 / 50–95 |
| Maize | 20–30 / 15–35 | 50–75 / 40–85 | 40–65 / 30–75 |
| Cotton | 25–35 / 18–40 | 40–65 / 30–75 | 40–65 / 30–75 |
| Sugarcane | 25–35 / 20–40 | 60–80 / 50–90 | 50–75 / 40–85 |
| Groundnut | 25–32 / 20–38 | 50–70 / 40–80 | 40–60 / 30–70 |
| Mustard | 15–25 / 10–30 | 40–65 / 30–75 | 35–55 / 25–65 |
| Chickpea | 18–28 / 12–32 | 40–60 / 30–70 | 35–55 / 25–65 |
| Pearl Millet | 25–35 / 20–40 | 40–65 / 30–75 | 30–55 / 20–65 |
| Soybeans | 20–30 / 15–35 | 55–80 / 45–90 | 45–70 / 35–80 |
| Tomato | 20–28 / 15–32 | 60–80 / 50–90 | 50–70 / 40–80 |

Advice text is shared through `defaults.advice` (with `{crop}` and `{value}`
placeholders); Wheat, Cotton and Soybeans have crop-specific overrides. Add an
`advice` block to any crop to customise its sentences.

> **Agronomy disclaimer.** These are typical values for Indian conditions and
> are meant as a working prototype baseline. **They must be checked against a
> local agronomy reference (ICAR package of practices, state agriculture
> department bulletins, or the guide's recommendations) before the review.**
> Every number and every advice sentence is in `crops.json`, so tuning them does
> not require a code change.

Each advice string accepts a `{value}` placeholder, e.g.
`"soil_moisture_low": "Soil moisture is low for Wheat (measured {value} %); irrigate within 24 hours."`
Values outside the marginal band get a `Critical:` prefix.

---

## 8. Simulation mode

```bash
SIMULATE=true uvicorn index:app --app-dir api --reload --port 8000
```

Synthetic readings follow a mean-reverting random walk with a gentle day/night
temperature curve, so the cards move realistically and all three crops can be
demonstrated without hardware. Simulation is recorded in the advisory
`fusion_note` ("Demo mode …") and exposed as `"simulate": true` by `/api/live`.

To switch it off, set `SIMULATE=false` (or delete the variable) and restart.
Nothing else changes: real uploads are accepted in both modes.

---

## 9. Tests and the quality gate

```bash
bash run_checks.sh          # flake8 -> pytest -> credential scan
```

The script prints a `PASS:` or `FAIL:` line per check and exits non-zero if any
check fails. It automatically uses `.venv` when present; override with
`PYTHON=/path/to/python bash run_checks.sh`.

* **flake8** – style, unused imports, obvious bugs (`api/` and `tests/`).
* **pytest** – 103 tests: fusion fallback cases, all three verdicts for every
  catalogue crop, weather parsing/caching, every endpoint, persistence, simulation.
  Tests use an in-memory SQLite database and a stubbed weather client, so they
  need no PostgreSQL server and make no network calls. The decision-matrix cases
  are generated from `crops.json`, so every catalogue crop is covered.
* **credential scan** – fails if a database URL with a password, an API key,
  password or token assignment, an OpenAI-style key or an AWS key appears in
  `api/`, `public/` or `firmware/`. It prints file and line numbers only, never
  the matched value.

Run only the tests with `pytest` (or `.venv/Scripts/python.exe -m pytest` on
Windows).

---

## 10. ESP32 firmware

File: `firmware/agricloud_esp32.ino`.

**Hardware:** ESP32 Dev Module, capacitive soil moisture sensor v1.2, DHT11,
10 kΩ resistor, 100 nF capacitor, breadboard and jumpers.

| Sensor | Pin |
|---|---|
| Soil sensor `AOUT` | GPIO 34 (ADC1), `VCC` 3V3, `GND` GND |
| DHT11 `DATA` | GPIO 4, with 10 kΩ pull-up to 3V3 |

**Calibration:** hold the probe in dry air and note the raw ADC value (≈3200),
then in water (≈1300). Put those in `DRY_VALUE` and `WET_VALUE`. The firmware
maps `DRY_VALUE → 0 %` and `WET_VALUE → 100 %` and clamps to 0–100.

**Behaviour:** every 5 minutes it takes 5 samples (soil on each pass, DHT11 once
per pass because the sensor needs about 1 s between reads), averages them,
prints debug lines to the Serial Monitor and posts JSON over HTTPS with the
`X-API-Key` header. If Wi-Fi is down it still senses, logs that the upload was
skipped, and retries on the next cycle.

**Fill these in** at the top of the sketch: `WIFI_SSID`, `WIFI_PASSWORD`,
`API_KEY`, `SERVER_HOST` (host only, e.g. `agri-cloud-ci.vercel.app`).
They are empty in the repository on purpose. TLS uses `setInsecure()` for the
demo; for a real deployment set `TLS_INSECURE = false` and install the CA with
`client.setCACert(...)`.

**Flash:** open the sketch in Arduino IDE, install *DHT sensor library* and
*Adafruit Unified Sensor*, select *ESP32 Dev Module*, then upload and open the
Serial Monitor at 115200 baud.

---

## 11. Deploying to Vercel

1. Push the repository to GitHub.
2. In Vercel, **Add New → Project** and import the repository. `vercel.json`
   already builds `api/index.py` and serves `public/`.
3. Create a PostgreSQL database with your managed provider and copy its
   connection string.
4. Add the environment variables in **Project → Settings → Environment
   Variables** for Production (and Preview if you use it):
   `DEVICE_API_KEY`, `DATABASE_URL`, `LATITUDE`, `LONGITUDE`, `SIMULATE=false`.
5. Deploy. The audit table is created on the first request.

Command-line alternative:

```bash
npm install -g vercel
vercel login
vercel link
vercel env add DEVICE_API_KEY production
vercel env add DATABASE_URL production
vercel env add LATITUDE production
vercel env add LONGITUDE production
vercel env add SIMULATE production
vercel --prod
```

Do **not** put the connection string or the device key in `vercel.json`, the
repository or the firmware source that you publish.

---

## 12. Continuous delivery

`.github/workflows/ci.yml` runs on every push and pull request:

1. install `requirements-dev.txt` on Python 3.11;
2. run `bash run_checks.sh` (flake8, pytest, credential scan);
3. only if the gate passed, and only on `main`, deploy with the Vercel CLI.

Add three repository secrets for the deploy job:
`VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` (from `vercel link` /
`vercel project ls`). If they are absent the workflow prints a notice and skips
deployment instead of failing the build.

---

## 13. Viva notes and limitations

* **Why is fusion at read time?** The 15 minute freshness and "since the last
  reading" spike rules are evaluated against the current moment, so a stored
  upload never needs to know whether it will later be trusted.
* **Why cache weather for 10 minutes?** Free API, one shared observation per
  grid point; the dashboard refreshes every 30 seconds but the upstream call is
  bounded to six per hour per warm instance.
* **Why SQLite in tests?** The SQLAlchemy model is portable, so endpoint tests
  need no external service and stay fast and deterministic. Production uses
  PostgreSQL through `DATABASE_URL`.
* **Why the credential scan?** The CI gate must fail on a committed secret, so
  it is checked mechanically instead of relying on reviewers.
* **Limitations:** the capacitive probe reports a relative percentage, not
  volumetric water content, so satellite and device soil moisture are compared
  only at the advisory level, not calibrated against each other. The DHT11 is
  ±2 °C / ±5 % RH, and TLS is simplified in the demo firmware. The agronomic
  bands need local validation (see the disclaimer).
* **Future scope:** solar power, LoRaWAN backhaul, NPK estimation, SMS/WhatsApp
  advisories, and automatic probe calibration.

---

## 14. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `DATABASE_URL is not set` | Copy `.env.example` to `.env`, or export the variable. The API loads `.env` automatically in local development. |
| `401 Invalid or missing X-API-Key` | The header must exactly equal `DEVICE_API_KEY`; restart the API after changing `.env`. |
| `503 … Open-Meteo is unavailable` | No fresh ESP32 reading and the weather call failed. Check the latitude/longitude and outbound HTTPS access to `api.open-meteo.com`. |
| `psycopg2` build error | Use `psycopg2-binary` from `requirements.txt`; it ships wheels for Python 3.11/3.12. |
| Dashboard shows an error banner | Open `/api/health`; if the API is fine, check the browser console for a blocked CDN (Tailwind) or an ad-blocker. |
| Docker password error | `POSTGRES_PASSWORD` has no default by design; set it in `.env`. |
