import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus.recovery import recoverable_delete, list_recovery, restore_file
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


if __name__ == "__main__":
    unittest.main()
