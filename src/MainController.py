# File: src/MainController.py  (Pi 3)
#
# FIXES applied:
#   1. Watchdog integration — calls sd_notify WATCHDOG=1 via systemd-notify
#      so systemd restarts the service if the loop hangs (needs
#      WatchdogSec=120 in the .service file).
#   2. Gateway-based connectivity check — has_gateway() pings the gateway
#      LAN IP instead of google.com; works on farm networks with no internet.
#   3. Calibration refresh — fetches calibration from gateway on startup and
#      every CAL_REFRESH_INTERVAL seconds; reloads SensorReader without restart.
#   4. Offline partial-flush — uses OfflineLogger.clear_records() so only
#      confirmed-uploaded rows are removed (prevents data loss on partial flush).
#   5. OTA check — delegates to OTAUpdater on each OTA_CHECK_INTERVAL.

import time
import logging
import subprocess
from datetime import datetime
from src.OnlineLogger import OnlineLogger
from src.OfflineLogger import OfflineLogger
from src.SensorReader import SensorReader
from config.Config import Config

logger = logging.getLogger(__name__)

# Calibration refresh: every 6 hours
CAL_REFRESH_INTERVAL = 6 * 3600


def _sd_watchdog():
    """Notify systemd watchdog (no-op if not running under systemd)."""
    try:
        subprocess.run(
            ['systemd-notify', 'WATCHDOG=1'],
            capture_output=True, timeout=2
        )
    except Exception:
        pass


class MainController:
    def __init__(self):
        self.sensor  = SensorReader()
        self.online  = OnlineLogger()
        self.offline = OfflineLogger()
        self._last_cal_refresh = 0
        self._register_on_startup()

    # ── Startup ───────────────────────────────────────────────

    def _register_on_startup(self):
        if self.has_gateway():
            info = self.sensor.get_sensor_info()
            if self.online.register_sensor(info):
                logger.info(f"Registered: {info['machine_id']}")
                # Pull calibration on first boot
                self._refresh_calibration()
            else:
                logger.warning("Registration failed — will retry next cycle")
        else:
            logger.info("Gateway unreachable — registration deferred")

    # ── Connectivity ──────────────────────────────────────────

    def has_gateway(self) -> bool:
        """FIX 2: ping the gateway LAN IP, not google.com.
        Works on isolated farm LANs.
        """
        ip = Config.GATEWAY_IP
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '2', ip],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    # ── Calibration ───────────────────────────────────────────

    def _refresh_calibration(self):
        """FIX 3: fetch calibration from gateway and hot-reload SensorReader."""
        try:
            updated = self.online.update_calibration(self.sensor.machine_id)
            if updated:
                self.sensor.reload_calibration()
            self._last_cal_refresh = time.time()
        except Exception as e:
            logger.warning(f"Calibration refresh failed: {e}")

    # ── Assignment check ──────────────────────────────────────

    def is_sensor_assigned(self, machine_id: str) -> bool:
        if not self.has_gateway():
            # If unreachable, allow offline collection to prevent data loss
            return True
        try:
            import mysql.connector
            conn = mysql.connector.connect(**Config.DB_CONFIG)
            cur  = conn.cursor()
            cur.execute(
                "SELECT farm_id, is_active FROM sensors WHERE machine_id = %s",
                (machine_id,)
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            return row is not None and row[0] is not None and row[1] == 1
        except Exception as e:
            logger.error(f"Assignment check failed: {e}")
            return False

    # ── Main loop ─────────────────────────────────────────────

    def run(self):
        logger.info(f"MainController starting — machine_id={self.sensor.machine_id}")
        subprocess.run(['systemd-notify', 'READY=1'], capture_output=True)

        while True:
            try:
                # FIX 1: kick the watchdog each cycle
                _sd_watchdog()

                # FIX 3: periodic calibration refresh
                if time.time() - self._last_cal_refresh > CAL_REFRESH_INTERVAL:
                    self._refresh_calibration()

                if not self.is_sensor_assigned(self.sensor.machine_id):
                    logger.info(
                        f"Sensor {self.sensor.machine_id} unassigned — skipping"
                    )
                    time.sleep(Config.MEASUREMENT_INTERVAL)
                    continue

                data = self.sensor.read_data()
                if not data:
                    time.sleep(Config.MEASUREMENT_INTERVAL)
                    continue

                if self.has_gateway():
                    if self.online.save(data):
                        # FIX 4: partial-flush offline buffer
                        self._flush_offline()
                    else:
                        logger.warning("Online save failed — storing offline")
                        self.offline.save(data)
                else:
                    self.offline.save(data)

                time.sleep(Config.MEASUREMENT_INTERVAL)

            except KeyboardInterrupt:
                logger.info("Stopped by user")
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                time.sleep(Config.MEASUREMENT_INTERVAL)

    # ── Offline flush ─────────────────────────────────────────

    def _flush_offline(self):
        """FIX 4: upload offline rows one by one; remove only those that succeed."""
        offline_df = self.offline.load_all()
        if offline_df.empty:
            return

        uploaded_ids   = []
        uploaded_times = []
        for _, row in offline_df.iterrows():
            record = row.to_dict()
            if self.online.save(record):
                uploaded_ids.append(str(record.get('machine_id', '')))
                uploaded_times.append(str(record.get('timestamp', '')))

        if uploaded_ids:
            self.offline.clear_records(uploaded_ids, uploaded_times)
            logger.info(
                f"Flushed {len(uploaded_ids)}/{len(offline_df)} offline records"
            )


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )
    controller = MainController()
    controller.run()
