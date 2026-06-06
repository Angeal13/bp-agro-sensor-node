"""
tests/test_sensor_node.py  — Pi 3 Sensor Node Test Suite
Run with:  pytest tests/ -v
All tests run without real hardware (serial, GPIO, MySQL mocked).
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch, mock_open

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub hardware modules before importing production code
sys.modules.setdefault('serial',            MagicMock())
sys.modules.setdefault('RPi',               MagicMock())
sys.modules.setdefault('RPi.GPIO',          MagicMock())
sys.modules.setdefault('mysql',             MagicMock())
sys.modules.setdefault('mysql.connector',   MagicMock())
import pandas  # ensure real pandas is present for OfflineLogger

# ── Config ──────────────────────────────────────────────────

class TestConfig(unittest.TestCase):
    def test_machine_id_reads_etc_machine_id(self):
        from config.Config import Config
        with patch('builtins.open', mock_open(read_data='aabbcc00ddeeff00\n')):
            mid = Config.get_machine_id()
        self.assertEqual(mid, 'aabbcc00ddeeff00')

    def test_machine_id_fallback_uuid(self):
        from config.Config import Config
        with patch('builtins.open', side_effect=OSError), \
             patch('uuid.getnode', return_value=0xAABBCCDDEEFF):
            mid = Config.get_machine_id()
        self.assertIsInstance(mid, str)
        self.assertTrue(len(mid) > 0)

# ── OfflineLogger ────────────────────────────────────────────

class TestOfflineLogger(unittest.TestCase):
    def setUp(self):
        self.tmpfile = tempfile.mktemp(suffix='.csv')

    def tearDown(self):
        for p in [self.tmpfile, self.tmpfile + '.tmp']:
            try: os.unlink(p)
            except FileNotFoundError: pass

    def _make(self):
        with patch('config.Config.Config.OFFLINE_STORAGE', self.tmpfile), \
             patch('config.Config.Config.MAX_OFFLINE_RECORDS', 5):
            from importlib import reload
            import src.OfflineLogger as m; reload(m)
            ol = m.OfflineLogger()
            ol.storage_path = self.tmpfile
            return ol

    def _row(self, mid='S1', ts='2024-01-01 00:00:00'):
        return {'machine_id': mid, 'timestamp': ts,
                'moisture': 55.0, 'temperature': 22.0, 'conductivity': 1.0,
                'ph': 6.5, 'nitrogen': 100.0, 'phosphorus': 30.0, 'potassium': 120.0}

    def test_save_and_load(self):
        ol = self._make()
        ol.save(self._row())
        df = ol.load_all()
        self.assertEqual(len(df), 1)

    def test_deduplication(self):
        ol = self._make()
        r  = self._row()
        ol.save(r); ol.save(r)
        self.assertEqual(len(ol.load_all()), 1)

    def test_eviction(self):
        from config.Config import Config
        Config.MAX_OFFLINE_RECORDS = 3
        ol = self._make()
        for i in range(5):
            ol.save(self._row(mid=f'EV{i}', ts=f'2024-01-0{i+1} 00:00:00'))
        self.assertLessEqual(len(ol.load_all()), 3)

    def test_partial_clear(self):
        ol = self._make()
        for i in range(4):
            ol.save(self._row(ts=f'2024-01-01 00:0{i}:00'))
        ol.clear_records(['S1', 'S1'],
                         ['2024-01-01 00:00:00', '2024-01-01 00:01:00'])
        self.assertEqual(len(ol.load_all()), 2)

    def test_atomic_write(self):
        ol = self._make()
        ol.save(self._row())
        self.assertFalse(os.path.exists(self.tmpfile + '.tmp'))
        self.assertTrue(os.path.exists(self.tmpfile))

# ── SensorReader ─────────────────────────────────────────────

class TestSensorReader(unittest.TestCase):
    def _valid_response(self):
        vals = [550, 220, 10, 65, 1000, 300, 1200]
        buf = bytes([0x01, 0x03, 0x0E])
        for v in vals:
            buf += v.to_bytes(2, 'big')
        return (buf + bytes(2))[:19]

    @patch('config.Config.Config.RS485_RESET_GPIO', None)
    @patch('config.Config.Config.CALIBRATION_FILE', '/nonexistent/cal.json')
    @patch('config.Config.Config.get_machine_id', return_value='TMID')
    def test_parse_valid_response(self, _):
        import serial as _serial
        _serial.Serial.return_value.is_open = True
        _serial.Serial.return_value.read.return_value = self._valid_response()
        from importlib import reload
        import src.SensorReader as m; reload(m)
        sr = m.SensorReader()
        sr.serial_conn = _serial.Serial.return_value
        data = sr.read_data()
        self.assertIsNotNone(data)
        self.assertAlmostEqual(data['moisture'],    55.0)
        self.assertAlmostEqual(data['temperature'], 22.0)
        self.assertAlmostEqual(data['ph'],           6.5)

    @patch('config.Config.Config.RS485_RESET_GPIO', None)
    @patch('config.Config.Config.CALIBRATION_FILE', '/nonexistent/cal.json')
    @patch('config.Config.Config.get_machine_id', return_value='TMID')
    def test_short_response_returns_none(self, _):
        import serial as _serial
        _serial.Serial.return_value.is_open = True
        _serial.Serial.return_value.read.return_value = b'\x01\x03'
        from importlib import reload
        import src.SensorReader as m; reload(m)
        sr = m.SensorReader()
        sr.serial_conn = _serial.Serial.return_value
        self.assertIsNone(sr.read_data())

    def test_calibration_offset(self):
        from src.SensorReader import _apply_calibration
        cal = {'moisture': {'offset': 5.0, 'scale': 1.0}}
        self.assertAlmostEqual(_apply_calibration(50.0, cal, 'moisture'), 55.0)

    def test_calibration_missing_passthrough(self):
        from src.SensorReader import _apply_calibration
        self.assertAlmostEqual(_apply_calibration(42.0, {}, 'temperature'), 42.0)

# ── OTAUpdater ───────────────────────────────────────────────

class TestOTAUpdater(unittest.TestCase):
    def test_newer_versions(self):
        from src.OTAUpdater import _newer
        self.assertTrue(_newer('1.1.0', '1.0.0'))
        self.assertTrue(_newer('2.0.0', '1.9.9'))
        self.assertFalse(_newer('1.0.0', '1.0.0'))
        self.assertFalse(_newer('1.0.0', '1.0.1'))

    def test_verify_correct_hmac(self):
        from src.OTAUpdater import _verify_package
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'pkg'); path = f.name
        sha = hashlib.sha256(b'pkg').hexdigest()
        sig = hmac.new(b'secret', sha.encode(), hashlib.sha256).hexdigest()
        with patch('config.Config.Config.OTA_HMAC_SECRET', 'secret'):
            self.assertTrue(_verify_package(path, sha, sig))
        os.unlink(path)

    def test_verify_bad_hmac(self):
        from src.OTAUpdater import _verify_package
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'pkg'); path = f.name
        sha = hashlib.sha256(b'pkg').hexdigest()
        with patch('config.Config.Config.OTA_HMAC_SECRET', 'secret'):
            self.assertFalse(_verify_package(path, sha, 'wrong'))
        os.unlink(path)

    def test_disabled_when_no_url(self):
        with patch('config.Config.Config.OTA_ENABLED', True), \
             patch('config.Config.Config.OTA_VERSION_URL', ''):
            from importlib import reload
            import src.OTAUpdater as m; reload(m)
            ota = m.OTAUpdater()
            self.assertFalse(ota.check_and_apply())

# ── MainController ───────────────────────────────────────────

class TestMainController(unittest.TestCase):
    def _ctrl(self):
        ms = MagicMock()
        ms.machine_id = 'CTRL01'
        ms.read_data.return_value = {
            'machine_id': 'CTRL01', 'timestamp': '2024-01-01 00:00:00',
            'moisture': 55.0, 'temperature': 22.0, 'conductivity': 1.0,
            'ph': 6.5, 'nitrogen': 100.0, 'phosphorus': 30.0, 'potassium': 120.0,
        }
        mo = MagicMock(); mo.save.return_value = True
        mof = MagicMock(); mof.load_all.return_value = pandas.DataFrame()

        with patch('src.MainController.SensorReader',  return_value=ms), \
             patch('src.MainController.OnlineLogger',  return_value=mo), \
             patch('src.MainController.OfflineLogger', return_value=mof), \
             patch('src.MainController.MainController._register_on_startup'), \
             patch('subprocess.run'):
            from importlib import reload
            import src.MainController as mm; reload(mm)
            ctrl = mm.MainController.__new__(mm.MainController)
            ctrl.sensor = ms; ctrl.online = mo; ctrl.offline = mof
            ctrl._last_cal_refresh = time.time()
        return ctrl

    def test_gateway_up_calls_online_save(self):
        ctrl = self._ctrl()
        with patch.object(ctrl, 'has_gateway', return_value=True), \
             patch.object(ctrl, 'is_sensor_assigned', return_value=True), \
             patch.object(ctrl, '_flush_offline'), \
             patch('time.sleep', side_effect=KeyboardInterrupt), \
             patch('subprocess.run'):
            try: ctrl.run()
            except KeyboardInterrupt: pass
        ctrl.online.save.assert_called()

    def test_gateway_down_calls_offline_save(self):
        ctrl = self._ctrl()
        with patch.object(ctrl, 'has_gateway', return_value=False), \
             patch.object(ctrl, 'is_sensor_assigned', return_value=True), \
             patch('time.sleep', side_effect=KeyboardInterrupt), \
             patch('subprocess.run'):
            try: ctrl.run()
            except KeyboardInterrupt: pass
        ctrl.offline.save.assert_called()

    def test_ping_used_not_google(self):
        """Gateway check pings Config.GATEWAY_IP, not google.com."""
        ctrl = self._ctrl()
        from config.Config import Config
        Config.GATEWAY_IP = '10.0.0.5'
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.returncode = 0
            ctrl.has_gateway()
        args = mock_run.call_args[0][0]
        self.assertIn('10.0.0.5', args)
        self.assertNotIn('google.com', ' '.join(args))

if __name__ == '__main__':
    unittest.main(verbosity=2)
