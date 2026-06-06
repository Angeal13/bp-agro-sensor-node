# File: src/SensorReader.py  (Pi 3)
#
# FIXES applied:
#   1. machine_id  — reads from Config.get_machine_id() (/etc/machine-id),
#                    not uuid.getnode() (MAC-based, breaks on board swap).
#   2. RS-485 fault recovery — if serial init fails 3 times, optionally
#      toggles the transceiver reset GPIO before giving up.
#   3. Calibration offsets  — reads config/calibration.json and applies
#      per-parameter (offset + scale) before returning readings.
#   4. Per-device read isolation — exceptions are caught so one bad
#      response does not kill the whole polling cycle.

import serial
import time
import json
import os
import logging
from datetime import datetime
from config.Config import Config

logger = logging.getLogger(__name__)


def _load_calibration(path: str) -> dict:
    """Load calibration table: {param: {offset, scale}}.
    Returns empty dict if file absent or malformed (safe default = no correction).
    """
    try:
        if os.path.exists(path):
            data = json.loads(open(path).read())
            logger.info(f"Calibration loaded from {path}: {list(data.keys())}")
            return data
    except Exception as e:
        logger.warning(f"Could not load calibration file {path}: {e}")
    return {}


def _apply_calibration(value: float, cal: dict, param: str) -> float:
    """Apply offset + scale from calibration table, if present."""
    if not cal or param not in cal:
        return value
    c = cal[param]
    return round(value * c.get('scale', 1.0) + c.get('offset', 0.0), 2)


def _reset_rs485_transceiver():
    """Toggle the RS-485 transceiver reset GPIO to recover from bus fault.
    Only executes if RS485_RESET_GPIO is configured (non-None).
    Safe no-op if RPi.GPIO is not available (dev/test environment).
    """
    pin = Config.RS485_RESET_GPIO
    if pin is None:
        return
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)
        time.sleep(0.1)
        GPIO.output(pin, GPIO.HIGH)
        time.sleep(0.1)
        logger.info(f"RS-485 transceiver reset on GPIO {pin}")
    except ImportError:
        pass  # not on a Pi — skip silently
    except Exception as e:
        logger.warning(f"RS-485 GPIO reset failed: {e}")


class SensorReader:
    def __init__(self):
        # FIX 1: persistent machine_id from /etc/machine-id
        self.machine_id = Config.get_machine_id()
        logger.info(f"SensorReader machine_id: {self.machine_id}")

        # FIX 3: load calibration offsets
        self._cal = _load_calibration(Config.CALIBRATION_FILE)

        # FIX 2: serial init with transceiver reset on repeated failure
        self.serial_conn = self._init_serial()

    # ── Serial init ───────────────────────────────────────────

    def _init_serial(self, reset_on_fail: bool = True):
        """Attempt to open the serial port up to 3 times.
        On the third failure, try resetting the RS-485 transceiver
        via GPIO before returning None.
        """
        for attempt in range(3):
            try:
                ser = serial.Serial(
                    port=Config.SERIAL_PORT,
                    baudrate=Config.SERIAL_BAUDRATE,
                    timeout=Config.SERIAL_TIMEOUT,
                )
                logger.info(f"Serial connected: {Config.SERIAL_PORT}")
                return ser
            except serial.SerialException as e:
                logger.warning(f"Serial attempt {attempt + 1}/3 failed: {e}")
                if attempt == 1 and reset_on_fail:
                    _reset_rs485_transceiver()
                time.sleep(2)
        logger.error("Serial init failed after 3 attempts")
        return None

    # ── Public API ────────────────────────────────────────────

    def get_sensor_info(self) -> dict:
        return {
            'machine_id':            self.machine_id,
            'connection_timestamp':  datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

    def read_data(self) -> dict | None:
        """Poll sensor via Modbus RTU, apply calibration, return reading dict.

        FIX 4: all exceptions isolated so one bad response does not kill
        the polling loop in MainController.
        """
        # Re-init serial if connection dropped
        if not self.serial_conn or not self.serial_conn.is_open:
            self.serial_conn = self._init_serial()
            if not self.serial_conn:
                return None

        try:
            self.serial_conn.reset_input_buffer()
            self.serial_conn.write(Config.MODBUS_COMMAND)
            response = self.serial_conn.read(Config.RESPONSE_LENGTH)

            if len(response) < Config.RESPONSE_LENGTH:
                logger.warning(
                    f"Short response: got {len(response)} bytes, "
                    f"expected {Config.RESPONSE_LENGTH}"
                )
                return None

            # Parse raw register values
            raw = {
                'moisture':    int.from_bytes(response[3:5],   'big') / 10.0,
                'temperature': int.from_bytes(response[5:7],   'big') / 10.0,
                'conductivity':int.from_bytes(response[7:9],   'big') / 10.0,
                'ph':          int.from_bytes(response[9:11],  'big') / 10.0,
                'nitrogen':    int.from_bytes(response[11:13], 'big') / 10.0,
                'phosphorus':  int.from_bytes(response[13:15], 'big') / 10.0,
                'potassium':   int.from_bytes(response[15:17], 'big') / 10.0,
            }

            # FIX 3: apply calibration
            cal_map = {
                'moisture':    'moisture',
                'temperature': 'temperature',
                'conductivity':'ec',
                'ph':          'ph',
                'nitrogen':    'nitrogen',
                'phosphorus':  'phosphorus',
                'potassium':   'potassium',
            }
            for key, cal_key in cal_map.items():
                raw[key] = _apply_calibration(raw[key], self._cal, cal_key)

            return {
                'machine_id':  self.machine_id,
                'timestamp':   datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                **raw,
            }

        except serial.SerialException as e:
            # FIX 4: serial fault — mark connection dead, MainController retries next cycle
            logger.error(f"Serial read failed (will retry next cycle): {e}")
            try:
                self.serial_conn.close()
            except Exception:
                pass
            self.serial_conn = None
            return None
        except Exception as e:
            # FIX 4: any other parsing error is isolated
            logger.error(f"Unexpected sensor read error: {e}")
            return None

    def reload_calibration(self):
        """Hot-reload calibration file without restarting the process."""
        self._cal = _load_calibration(Config.CALIBRATION_FILE)
        logger.info("Calibration reloaded")
