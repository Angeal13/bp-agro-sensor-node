# Changelog

All notable changes to this project are documented here.
Format: [Semantic Versioning](https://semver.org/)

---

## [Unreleased]
- Cybersecurity hardening (HMAC enforcement, SSL/TLS, per-node auth)

## [2.0.0] — 2025-06
### Added
- `OTAUpdater` — S3-based firmware updates with SHA-256 + HMAC-SHA256 verification
- `calibration.json` support — per-parameter offset and scale applied to all readings
- RS-485 transceiver GPIO reset on serial init failure (brown-out recovery)
- Watchdog integration — `systemd-notify WATCHDOG=1` each polling cycle
- `OfflineLogger.clear_records()` — partial flush removes only confirmed-uploaded rows

### Fixed
- `machine_id` now reads from `/etc/machine-id` (stable across board swaps)
- Gateway connectivity check pings gateway LAN IP, not google.com
- Offline CSV: atomic write via temp file + rename, oldest-first eviction cap
- `OnlineLogger.save()` reconnects on stale MySQL connection automatically

## [1.0.0] — 2025-01
### Added
- Initial release: Modbus RTU read, online/offline logging, gateway registration
