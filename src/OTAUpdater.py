# File: src/OTAUpdater.py  (Pi 3)
#
# NEW FILE — OTA update client for sensor nodes.
#
# Workflow:
#   1. Fetch VERSION file from OTA_VERSION_URL (S3 or EC2)
#   2. Compare to local /etc/bpagro_version
#   3. If newer: download package, verify HMAC-SHA256 signature
#   4. Extract to /tmp/bpagro_update, run install.sh
#   5. systemctl restart pi3-sensor
#
# The server signs each package with HMAC-SHA256(OTA_HMAC_SECRET, sha256(package)).
# The VERSION file format:
#   {"version": "2.1.3", "url": "https://...", "sha256": "abc...", "hmac": "xyz..."}

import os
import hashlib
import hmac
import json
import logging
import shutil
import subprocess
import tarfile
import tempfile
import requests
from config.Config import Config

logger = logging.getLogger(__name__)

VERSION_FILE = '/etc/bpagro_version'
UPDATE_DIR   = '/tmp/bpagro_update'


def _local_version() -> str:
    try:
        return open(VERSION_FILE).read().strip()
    except FileNotFoundError:
        return '0.0.0'


def _newer(remote: str, local: str) -> bool:
    """Simple semver comparison (major.minor.patch)."""
    def parts(v):
        try:
            return tuple(int(x) for x in v.split('.')[:3])
        except ValueError:
            return (0, 0, 0)
    return parts(remote) > parts(local)


def _verify_package(path: str, expected_sha256: str, hmac_sig: str) -> bool:
    """Verify file integrity (SHA-256) and authenticity (HMAC)."""
    data = open(path, 'rb').read()

    # SHA-256 integrity check
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != expected_sha256:
        logger.error(
            f"OTA SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
        return False

    # HMAC-SHA256 authenticity check
    secret = Config.OTA_HMAC_SECRET
    if not secret:
        logger.warning("OTA_HMAC_SECRET not set — skipping HMAC check (insecure)")
        return True

    expected_hmac = hmac.new(
        secret.encode(), actual_sha256.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_hmac, hmac_sig):
        logger.error("OTA HMAC signature invalid — package rejected")
        return False

    logger.info("OTA package verified OK")
    return True


class OTAUpdater:
    """Check for and apply OTA updates."""

    def check_and_apply(self) -> bool:
        """Return True if an update was applied (caller should restart service)."""
        if not Config.OTA_ENABLED or not Config.OTA_VERSION_URL:
            return False

        local_ver = _local_version()
        try:
            r = requests.get(Config.OTA_VERSION_URL, timeout=10)
            if not r.ok:
                return False
            info = r.json()
        except Exception as e:
            logger.debug(f"OTA version check failed: {e}")
            return False

        remote_ver = info.get('version', '0.0.0')
        if not _newer(remote_ver, local_ver):
            logger.debug(f"OTA: up to date ({local_ver})")
            return False

        logger.info(f"OTA update available: {local_ver} → {remote_ver}")

        # Download package
        pkg_url = info.get('url', Config.OTA_PACKAGE_URL)
        if not pkg_url:
            logger.warning("OTA: no package URL in version manifest")
            return False

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.tar.gz') as tmp:
                pkg_path = tmp.name
                resp = requests.get(pkg_url, stream=True, timeout=60)
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=65536):
                    tmp.write(chunk)
        except Exception as e:
            logger.error(f"OTA download failed: {e}")
            return False

        # Verify
        if not _verify_package(pkg_path, info.get('sha256', ''), info.get('hmac', '')):
            os.unlink(pkg_path)
            return False

        # Extract
        try:
            if os.path.exists(UPDATE_DIR):
                shutil.rmtree(UPDATE_DIR)
            os.makedirs(UPDATE_DIR)
            with tarfile.open(pkg_path, 'r:gz') as tar:
                tar.extractall(UPDATE_DIR)
            os.unlink(pkg_path)
        except Exception as e:
            logger.error(f"OTA extract failed: {e}")
            return False

        # Run install script
        install_script = os.path.join(UPDATE_DIR, 'install.sh')
        if not os.path.exists(install_script):
            logger.error("OTA: install.sh not found in package")
            return False

        try:
            result = subprocess.run(
                ['bash', install_script],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                logger.error(f"OTA install.sh failed:\n{result.stderr}")
                return False
            logger.info(f"OTA install.sh output:\n{result.stdout}")
        except subprocess.TimeoutExpired:
            logger.error("OTA install.sh timed out")
            return False
        except Exception as e:
            logger.error(f"OTA install error: {e}")
            return False

        # Write new version file
        open(VERSION_FILE, 'w').write(remote_ver)
        logger.info(f"OTA complete: now at {remote_ver} — restarting service")

        # Restart via systemd (non-blocking — systemd will restart the unit)
        subprocess.Popen(['systemctl', 'restart', 'pi3-sensor'])
        return True
