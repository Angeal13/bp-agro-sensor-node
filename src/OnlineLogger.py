# File: src/OnlineLogger.py  (Pi 3)
#
# FIXES applied:
#   1. Reconnect on stale connection — checks is_connected() before every
#      operation; reconnects rather than silently failing.
#   2. INSERT IGNORE — duplicate (machine_id, timestamp) rows are silently
#      skipped so offline flush re-uploads never create duplicates in Aurora.
#   3. Calibration endpoint — new update_calibration() fetches the node's
#      calibration table from the gateway API and writes config/calibration.json
#      so SensorReader picks it up on the next reload_calibration() call.

import mysql.connector
import requests
import json
import logging
import time
from config.Config import Config

logger = logging.getLogger(__name__)


class OnlineLogger:
    def __init__(self):
        self.conn = self._connect()

    # ── Connection ────────────────────────────────────────────

    def _connect(self):
        for attempt in range(3):
            try:
                c = mysql.connector.connect(**Config.DB_CONFIG)
                logger.info("OnlineLogger: connected to gateway MySQL")
                return c
            except mysql.connector.Error as e:
                logger.warning(f"DB connect attempt {attempt + 1}/3: {e}")
                if attempt < 2:
                    time.sleep(5)
        logger.error("OnlineLogger: failed to connect after 3 attempts")
        return None

    def _ensure_conn(self) -> bool:
        """Re-establish connection if dropped."""
        try:
            if self.conn and self.conn.is_connected():
                return True
        except Exception:
            pass
        self.conn = self._connect()
        return self.conn is not None

    # ── Registration ─────────────────────────────────────────

    def register_sensor(self, sensor_info: dict) -> bool:
        if not self._ensure_conn():
            return False
        try:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM sensors WHERE machine_id = %s",
                (sensor_info['machine_id'],)
            )
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "INSERT INTO sensors (machine_id, installation) VALUES (%s, NULL)",
                    (sensor_info['machine_id'],)
                )
                self.conn.commit()
                logger.info(f"Sensor registered: {sensor_info['machine_id']}")
            else:
                logger.info(f"Sensor already registered: {sensor_info['machine_id']}")
            cur.close()
            return True
        except mysql.connector.Error as e:
            logger.error(f"register_sensor failed: {e}")
            self.conn.rollback()
            return False

    # ── Save ──────────────────────────────────────────────────

    def save(self, data: dict) -> bool:
        """Write one reading. FIX 2: INSERT IGNORE prevents duplicates on
        re-upload of offline data (requires UNIQUE KEY on machine_id+timestamp
        in the gateway's soildata table — added in pi4_schema.sql).
        """
        if not self._ensure_conn():
            return False
        try:
            cur = self.conn.cursor()
            # FIX 2: INSERT IGNORE — safe to replay offline records
            cur.execute(
                """INSERT IGNORE INTO soildata
                       (machine_id, timestamp, moisture, temperature,
                        ec, ph, n, p, k)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    data['machine_id'],
                    data['timestamp'],
                    data['moisture'],
                    data['temperature'],
                    data['conductivity'],   # maps to ec
                    data['ph'],
                    data['nitrogen'],
                    data['phosphorus'],
                    data['potassium'],
                )
            )
            self.conn.commit()
            cur.close()
            logger.debug(f"Saved reading for {data['machine_id']}")
            return True
        except mysql.connector.Error as e:
            logger.error(f"save failed: {e}")
            try:
                self.conn.rollback()
            except Exception:
                pass
            # If foreign key error, register sensor then retry once
            if 'foreign key' in str(e).lower():
                logger.info("Attempting sensor registration due to FK error")
                if self.register_sensor({'machine_id': data['machine_id']}):
                    return self.save(data)
            return False
        except Exception as e:
            logger.error(f"Unexpected save error: {e}")
            return False

    # ── Calibration ───────────────────────────────────────────

    def update_calibration(self, machine_id: str) -> bool:
        """FIX 3: fetch this node's calibration table from gateway API
        and persist it to CALIBRATION_FILE for SensorReader to use.
        Endpoint: GET /api/local/sensor/<machine_id>/calibration
        Response: {"calibration": {"moisture": {"offset": 0, "scale": 1}, ...}}
        """
        url = f"{Config.GATEWAY_API}/api/local/sensor/{machine_id}/calibration"
        try:
            r = requests.get(url, timeout=5)
            if not r.ok:
                logger.warning(f"Calibration fetch HTTP {r.status_code}")
                return False
            cal = r.json().get('calibration', {})
            if not cal:
                logger.info("No calibration data returned from gateway")
                return False
            with open(Config.CALIBRATION_FILE, 'w') as f:
                json.dump(cal, f, indent=2)
            logger.info(f"Calibration updated: {list(cal.keys())}")
            return True
        except Exception as e:
            logger.warning(f"update_calibration failed: {e}")
            return False
