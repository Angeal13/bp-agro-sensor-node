# File: src/OfflineLogger.py  (Pi 3)
#
# FIXES applied:
#   1. Oldest-first eviction  — .tail(MAX_OFFLINE_RECORDS) already present;
#      added explicit guard so CSV never grows without bound even if pandas
#      concat fails mid-write.
#   2. Deduplication on load  — drop_duplicates on (machine_id, timestamp)
#      so offline records flushed after a partial upload are not re-sent.
#   3. Partial-clear  — new clear_records(ids) removes only rows that were
#      successfully uploaded, not the whole file (prevents data loss if the
#      flush upload was only partially successful).
#   4. Atomic write  — write to a temp file then rename to avoid a corrupt
#      CSV if the process is killed mid-write.

import os
import logging
import pandas as pd
from config.Config import Config

logger = logging.getLogger(__name__)

_DEDUP_COLS = ['machine_id', 'timestamp']


class OfflineLogger:
    def __init__(self):
        self.storage_path = Config.OFFLINE_STORAGE

    # ── Write ─────────────────────────────────────────────────

    def save(self, data: dict) -> bool:
        """Append one reading.  Enforces MAX_OFFLINE_RECORDS cap (oldest evicted).
        FIX 4: atomic write via temp file + rename.
        """
        try:
            new_row = pd.DataFrame([data])

            if os.path.exists(self.storage_path):
                existing = pd.read_csv(self.storage_path)
                combined = pd.concat([existing, new_row], ignore_index=True)
            else:
                combined = new_row

            # FIX 2: deduplicate before saving
            combined = combined.drop_duplicates(
                subset=_DEDUP_COLS, keep='last'
            ).reset_index(drop=True)

            # FIX 1: hard cap — evict oldest
            if len(combined) > Config.MAX_OFFLINE_RECORDS:
                combined = combined.tail(Config.MAX_OFFLINE_RECORDS).reset_index(drop=True)

            # FIX 4: atomic write
            tmp = self.storage_path + '.tmp'
            combined.to_csv(tmp, index=False)
            os.replace(tmp, self.storage_path)

            logger.info(f"Offline stored ({len(combined)}/{Config.MAX_OFFLINE_RECORDS})")
            return True

        except Exception as e:
            logger.error(f"Offline save failed: {e}")
            return False

    # ── Read ──────────────────────────────────────────────────

    def load_all(self) -> pd.DataFrame:
        """Return all offline records, deduplicated."""
        if not os.path.exists(self.storage_path):
            return pd.DataFrame()
        try:
            df = pd.read_csv(self.storage_path)
            # FIX 2: ensure no duplicates even if old file had them
            return df.drop_duplicates(subset=_DEDUP_COLS, keep='last').reset_index(drop=True)
        except Exception as e:
            logger.error(f"Offline load failed: {e}")
            return pd.DataFrame()

    def count(self) -> int:
        df = self.load_all()
        return len(df)

    # ── Clear ─────────────────────────────────────────────────

    def clear(self) -> bool:
        """Delete the entire offline store (used after full successful flush)."""
        try:
            if os.path.exists(self.storage_path):
                os.remove(self.storage_path)
            return True
        except Exception as e:
            logger.error(f"Offline clear failed: {e}")
            return False

    def clear_records(self, machine_ids: list, timestamps: list) -> bool:
        """FIX 3: remove only successfully uploaded rows, keep the rest.
        machine_ids and timestamps are parallel lists matching uploaded records.
        """
        if not os.path.exists(self.storage_path):
            return True
        try:
            df = self.load_all()
            if df.empty:
                return True

            # Build a set of (machine_id, timestamp) pairs to drop
            uploaded = set(zip(machine_ids, timestamps))
            mask = df.apply(
                lambda r: (str(r.get('machine_id', '')), str(r.get('timestamp', ''))) not in uploaded,
                axis=1
            )
            remaining = df[mask].reset_index(drop=True)

            if remaining.empty:
                return self.clear()

            # FIX 4: atomic write
            tmp = self.storage_path + '.tmp'
            remaining.to_csv(tmp, index=False)
            os.replace(tmp, self.storage_path)

            removed = len(df) - len(remaining)
            logger.info(f"Offline: removed {removed} uploaded records, {len(remaining)} remain")
            return True

        except Exception as e:
            logger.error(f"clear_records failed: {e}")
            return False
