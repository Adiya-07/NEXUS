import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus.executor import execute_tool_call
from nexus.path_policy import AccessPolicy, PathDenied
from nexus.undo import list_undo, undo_action, handle_undo
from nexus.recovery import list_recovery
from nexus.tools import TOOL_REGISTRY


class UndoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.work = root / "work"
        self.work.mkdir()
        self.source = self.work / "original.txt"
        self.source.write_bytes(b"original contents")
        self.folder = self.work / "backup"
        self.folder.mkdir()
        self.policy = AccessPolicy({str(self.work): "read_write"})
        for mock in (
            patch("nexus.undo.UNDO_ROOT", root / "undo"),
            patch("nexus.recovery.RECOVERY_ROOT", root / "recovery"),
            patch("nexus.executor.AccessPolicy.load", return_value=self.policy),
            patch("nexus.executor.confirm", return_value=True),
            patch("nexus.executor._log"),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def perform(self, name, **args):
        result = execute_tool_call(name, args)
        self.assertIn("result", result, result)
        self.assertIn("Undo ID:", result["result"])
        return list_undo()[0]["id"]

    def reverse(self, undo_id, answer="UNDO"):
        return undo_action(undo_id, ask=lambda _: answer, show=lambda _: None)

    def rename(self):
        return self.perform("rename_file", source=str(self.source), new_name="renamed.txt")

    def test_rename_roundtrip(self):
        undo_id = self.rename()
        self.assertFalse(self.source.exists())
        self.reverse(undo_id)
        self.assertEqual(self.source.read_bytes(), b"original contents")
        self.assertFalse((self.work / "renamed.txt").exists())
        self.assertEqual(list_undo()[0]["state"], "done")
        with self.assertRaises(PathDenied):
            self.reverse(undo_id)

    def test_move_roundtrip(self):
        undo_id = self.perform("move_file", source=str(self.source),
                               destination_folder=str(self.folder))
        self.reverse(undo_id)
        self.assertTrue(self.source.exists())
        self.assertFalse((self.folder / self.source.name).exists())

    def test_copy_goes_to_recycle_bin(self):
        undo_id = self.perform("copy_file", source=str(self.source),
                               destination_folder=str(self.folder))
        self.reverse(undo_id)
        self.assertTrue(self.source.exists())
        self.assertFalse((self.folder / self.source.name).exists())
        self.assertEqual(len(list_recovery()), 1)

    def test_modified_destination_refused(self):
        undo_id = self.rename()
        dest = self.work / "renamed.txt"
        dest.write_bytes(b"edited")
        with self.assertRaises(PathDenied):
            self.reverse(undo_id)
        self.assertEqual(dest.read_bytes(), b"edited")

    def test_occupied_original_refused(self):
        undo_id = self.rename()
        self.source.write_bytes(b"new file")
        with self.assertRaises(PathDenied):
            self.reverse(undo_id)
        self.assertEqual(self.source.read_bytes(), b"new file")

    def test_cancel_and_restrict(self):
        undo_id = self.rename()
        self.reverse(undo_id, "no")
        self.assertEqual(list_undo()[0]["state"], "ready")
        self.policy.rules[str(self.source)] = "blocked"
        with self.assertRaises(PathDenied):
            self.reverse(undo_id)
        self.assertFalse(self.source.exists())

    def test_modified_copy_source_refused(self):
        undo_id = self.perform("copy_file", source=str(self.source),
                               destination_folder=str(self.folder))
        self.source.write_bytes(b"changed original")
        with self.assertRaises(PathDenied):
            self.reverse(undo_id)
        self.assertTrue((self.folder / self.source.name).exists())

    def test_rechecks_after_confirmation(self):
        undo_id = self.rename()
        dest = self.work / "renamed.txt"
        def approve(_):
            dest.write_bytes(b"changed during confirmation")
            return "UNDO"
        with self.assertRaises(PathDenied):
            undo_action(undo_id, ask=approve, show=lambda _: None)
        self.assertFalse(self.source.exists())

    def test_declined_action_has_no_undo_record(self):
        with patch("nexus.executor.confirm", return_value=False):
            execute_tool_call("rename_file", {"source": str(self.source), "new_name": "renamed.txt"})
        self.assertEqual(list_undo(), [])

    def test_invalid_id_and_model_exclusion(self):
        self.assertNotIn("undo", TOOL_REGISTRY)
        self.assertTrue(handle_undo("undo ../bad", show=lambda _: None))
        self.assertIn("error", execute_tool_call("undo", {}))


if __name__ == "__main__":
    unittest.main()
