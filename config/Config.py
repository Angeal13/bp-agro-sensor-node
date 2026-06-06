# File: config/Config.py  (Pi 3)
# Auto-updated by pi3_bootstrap.py — do not edit GATEWAY_IP/DB host manually.
#
# FIX: machine_id now reads from /etc/machine-id (burned in by setup.sh)
#      instead of uuid.getnode() (MAC address).  Replacing the Pi 3 board
#      no longer changes the sensor identity.
#
# FIX: INTERNET_TEST_URLS removed.  Connectivity is now checked by probing
#      the gateway LAN IP directly (see MainController.has_gateway()).

import os

class Config:
    # ── Database (auto-updated by bootstrap) ─────────────────
    DB_CONFIG = {
        'user':     os.getenv('DB_USER',     'DevOps'),
        'password': os.getenv('DB_PASSWORD', 'DevTeam'),
        'host':     os.getenv('DB_HOST',     '192.168.1.100'),
        'port':     int(os.getenv('DB_PORT', '3306')),
        'database': os.getenv('DB_NAME',     'soilmonitornig'),
        'connection_timeout': 10,
        'autocommit': False,
    }

    # ── Gateway (auto-updated by bootstrap) ──────────────────
    GATEWAY_IP  = os.getenv('GATEWAY_IP',  '192.168.1.100')
    GATEWAY_API = os.getenv('GATEWAY_API', 'http://192.168.1.100:5001')

    # ── Serial / Modbus ───────────────────────────────────────
    SERIAL_PORT      = os.getenv('SERIAL_PORT', '/dev/ttyUSB0')
    SERIAL_BAUDRATE  = 9600
    SERIAL_TIMEOUT   = 1
    # 8-register NPK/pH/EC/moisture command (slave 0x01)
    MODBUS_COMMAND   = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x07, 0x04, 0x08])
    RESPONSE_LENGTH  = 19

    # ── RS-485 transceiver reset GPIO pin ────────────────────
    # FIX: hardware-reset pin for transceiver after brown-out.
    # Set to None to disable GPIO reset (e.g. on dev machine).
    RS485_RESET_GPIO = int(os.getenv('RS485_RESET_GPIO', '0')) or None

    # ── Behaviour ────────────────────────────────────────────
    MEASUREMENT_INTERVAL = int(os.getenv('MEASUREMENT_INTERVAL', '300'))  # 5 min

    # ── Offline storage ───────────────────────────────────────
    OFFLINE_STORAGE     = os.getenv('OFFLINE_STORAGE', 'offline_data.csv')
    # FIX: hard cap — oldest-first eviction in OfflineLogger.save()
    MAX_OFFLINE_RECORDS = int(os.getenv('MAX_OFFLINE_RECORDS', '1000'))

    # ── Machine identity ──────────────────────────────────────
    # FIX: read from /etc/machine-id (UUID burned in by setup.sh).
    # Fallback to CPU serial, then MAC, to handle dev environments.
    @staticmethod
    def get_machine_id() -> str:
        # 1. /etc/machine-id  — persistent across board swaps (preferred)
        try:
            mid = open('/etc/machine-id').read().strip()
            if mid:
                return mid
        except OSError:
            pass
        # 2. CPU serial from /proc/cpuinfo
        try:
            for line in open('/proc/cpuinfo'):
                if line.startswith('Serial'):
                    serial = line.split(':')[1].strip()
                    if serial and serial != '0000000000000000':
                        return serial
        except OSError:
            pass
        # 3. MAC address fallback (original behaviour)
        import uuid
        return str(uuid.getnode())

    # ── Calibration ───────────────────────────────────────────
    # FIX: path to calibration JSON written by QR/management app.
    CALIBRATION_FILE = os.getenv('CALIBRATION_FILE', 'config/calibration.json')

    # ── Auto-discovery ────────────────────────────────────────
    AUTO_DISCOVERY       = True
    FALLBACK_GATEWAY_IP  = os.getenv('GATEWAY_IP', '192.168.1.100')

    # ── OTA ───────────────────────────────────────────────────
    OTA_ENABLED         = os.getenv('OTA_ENABLED', 'true').lower() == 'true'
    OTA_CHECK_INTERVAL  = int(os.getenv('OTA_CHECK_INTERVAL', '3600'))  # 1 h
    OTA_VERSION_URL     = os.getenv('OTA_VERSION_URL', '')   # set by setup.sh
    OTA_PACKAGE_URL     = os.getenv('OTA_PACKAGE_URL', '')
    OTA_HMAC_SECRET     = os.getenv('OTA_HMAC_SECRET', '')
