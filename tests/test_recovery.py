import tempfile
import unittest
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from nexus.recovery import recoverable_delete, list_recovery, restore_file, cleanup_expired, handle_recovery, set_pinned, retention_days, _entry
from nexus.path_policy import AccessPolicy, PathDenied


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.work = root / "work"
        self.work.mkdir()
        self.file = self.work / "example.txt"
        self.file.write_bytes(b"test data")
        self.policy = AccessPolicy({str(self.work): "read_write"})
        for p in (patch("nexus.recovery.RECOVERY_ROOT", root / "recovery"),
                  patch("nexus.recovery.AccessPolicy.load", return_value=self.policy)):
            p.start()
            self.addCleanup(p.stop)

    def delete(self):
        recoverable_delete(str(self.file))
        self.assertFalse(self.file.exists())
        return list_recovery()[0][0]

    def test_restore_roundtrip(self):
        recovery_id = self.delete()
        restore_file(recovery_id, ask=lambda _: "RESTORE", show=lambda _: None)
        self.assertEqual(self.file.read_bytes(), b"test data")
        self.assertEqual(len(list_recovery()), 1)

    def test_restore_refuses_existing_file(self):
        recovery_id = self.delete()
        self.file.write_bytes(b"new data")
        with self.assertRaises(PathDenied):
            restore_file(recovery_id, ask=lambda _: "RESTORE", show=lambda _: None)
        self.assertEqual(self.file.read_bytes(), b"new data")

    def test_restore_cancel(self):
        recovery_id = self.delete()
        restore_file(recovery_id, ask=lambda _: "no", show=lambda _: None)
        self.assertFalse(self.file.exists())

    def test_restricted_restore(self):
        recovery_id = self.delete()
        self.policy.rules[str(self.file)] = "blocked"
        with self.assertRaises(PathDenied):
            restore_file(recovery_id, ask=lambda _: "RESTORE", show=lambda _: None)
        self.assertFalse(self.file.exists())

    def test_invalid_id(self):
        with self.assertRaises(PathDenied):
            restore_file("../outside")

    def test_failed_move_keeps_original(self):
        with patch("nexus.recovery.os.rename", side_effect=OSError("failure")):
            with self.assertRaises(OSError):
                recoverable_delete(str(self.file))
        self.assertEqual(self.file.read_bytes(), b"test data")
        self.assertEqual(list_recovery(), [])

    def expire(self, recovery_id):
        path = _entry(recovery_id) / "metadata.json"
        data = json.loads(path.read_text())
        data["expires_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        path.write_text(json.dumps(data))

    def test_expired_payload_removed_metadata_preserved(self):
        recovery_id = self.delete()
        self.expire(recovery_id)
        cleanup_expired(show=lambda _: None)
        self.assertEqual(list_recovery(), [])
        self.assertTrue((_entry(recovery_id) / "metadata.json").exists())

    def test_pin_protects_expired_file_and_empty(self):
        recovery_id = self.delete()
        set_pinned(recovery_id, True)
        self.expire(recovery_id)
        cleanup_expired(show=lambda _: None)
        handle_recovery("empty bin", ask=lambda _: "EMPTY", show=lambda _: None)
        self.assertEqual(len(list_recovery()), 1)

    def test_legacy_file_never_expires(self):
        recovery_id = self.delete()
        path = _entry(recovery_id) / "metadata.json"
        data = json.loads(path.read_text())
        del data["expires_at"]
        del data["pinned"]
        path.write_text(json.dumps(data))
        cleanup_expired(show=lambda _: None)
        self.assertEqual(len(list_recovery()), 1)

    def test_retention_change_requires_confirmation(self):
        handle_recovery("retention 14", ask=lambda _: "no", show=lambda _: None)
        self.assertEqual(retention_days(), 7)
        handle_recovery("retention 14", ask=lambda _: "CONFIRM", show=lambda _: None)
        self.assertEqual(retention_days(), 14)

    def test_empty_cancel_and_confirm(self):
        self.delete()
        handle_recovery("empty bin", ask=lambda _: "no", show=lambda _: None)
        self.assertEqual(len(list_recovery()), 1)
        handle_recovery("empty bin", ask=lambda _: "EMPTY", show=lambda _: None)
        self.assertEqual(list_recovery(), [])


if __name__ == "__main__":
    unittest.main()
