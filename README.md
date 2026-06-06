# bp-agro-soil-node

**BP Agro — Pi 3 Soil Sensor Node**

Reads NPK, pH, EC, moisture, and temperature from a soil sensor via Modbus RTU and writes readings to the farm gateway's local MySQL. Stores data offline when the gateway is unreachable and flushes on reconnect.

## Hardware
- Raspberry Pi 3 (any model with USB)
- NPK/pH/EC/moisture RS-485 Modbus sensor
- USB-to-RS-485 adapter

## Architecture
```
Soil Sensor (RS-485)
        │ Modbus RTU
   Pi 3 Node
   ├── SensorReader     reads 7 parameters, applies calibration
   ├── OnlineLogger     writes to gateway MySQL
   ├── OfflineLogger    buffers to CSV when gateway unreachable
   ├── OTAUpdater       pulls firmware updates from S3
   └── MainController   orchestrates polling loop + watchdog
        │
   Gateway LAN (TCP)
        │
   Gateway MySQL (soildata)
```

## Quick Start
```bash
git clone https://github.com/bp-agro/bp-agro-soil-node
cd bp-agro-soil-node
bash setup.sh
```

`setup.sh` will:
- Burn a stable UUID into `/etc/machine-id`
- Configure chrony NTP pointed at `gateway.local`
- Create a Python virtual environment
- Write `.env` from your inputs
- Install and start the `pi3-sensor` systemd service

## Configuration
Copy `.env.example` to `.env` and fill in:
```
GATEWAY_IP=192.168.1.100
GATEWAY_API=http://192.168.1.100:5001
DB_HOST=192.168.1.100
DB_USER=soil_user
DB_PASSWORD=secure_password
DB_NAME=soil_monitoring
SERIAL_PORT=/dev/ttyUSB0
MEASUREMENT_INTERVAL=300
OTA_VERSION_URL=          # leave blank to disable OTA
```

## Calibration
Place a `config/calibration.json` file (fetched automatically from the gateway API):
```json
{
  "moisture":    {"offset": 0.0, "scale": 1.0},
  "ph":          {"offset": 0.0, "scale": 1.0},
  "temperature": {"offset": 0.0, "scale": 1.0}
}
```

## Testing
```bash
pip install -r requirements.txt pytest
pytest tests/ -v
```

## Logs
```bash
journalctl -u pi3-sensor -f
```

## Repository Structure
```
src/
  MainController.py   — main polling loop, watchdog, offline flush
  SensorReader.py     — Modbus RTU read + calibration
  OnlineLogger.py     — gateway MySQL write + calibration fetch
  OfflineLogger.py    — CSV offline buffer with atomic write
  OTAUpdater.py       — S3-based OTA update client
  pi3_bootstrap.py    — auto-discovery and first-boot registration
config/
  Config.py                    — all configuration constants
  calibration.json.example     — calibration file template
services/
  pi3-sensor.service           — systemd unit with watchdog
tests/
  test_sensor_node.py          — full unit test suite (no hardware needed)
```
