import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus.path_policy import AccessPolicy, PathDenied, prepare_paths
from nexus.permission_commands import parse_permission, handle_permission
from nexus.executor import execute_tool_call
from nexus.tools import search_files, list_directory


class PathPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        self.private = self.allowed / "private"
        self.private.mkdir()
        self.source = self.allowed / "a.txt"
        self.source.write_text("test")
        (self.private / "secret.txt").write_text("secret")
        self.policy = AccessPolicy({str(self.allowed): "read_write",
                                    str(self.private): "blocked"})

    def test_default_deny_and_nested_restrictions(self):
        self.assertEqual(self.policy.check(self.source), self.source)
        for path in (self.root, self.private, self.private / "secret.txt",
                     self.allowed / ".." / "outside"):
            with self.assertRaises(PathDenied):
                self.policy.check(path)

    def test_read_only(self):
        policy = self.policy.changed(self.source, "read_only")
        policy.check(self.source)
        with self.assertRaises(PathDenied):
            policy.check(self.source, write=True)

    def test_destination_checks(self):
        for name, args in [
            ("copy_file", {"source": str(self.source), "destination_folder": str(self.private)}),
            ("move_file", {"source": str(self.source), "destination_folder": str(self.root)}),
            ("rename_file", {"source": str(self.source), "new_name": "a.txt"}),
            ("delete_file", {"path": str(self.allowed)}),
            ("search_files", {"directory": str(self.allowed), "pattern": "../*"}),
        ]:
            with self.subTest(name=name), self.assertRaises(PathDenied):
                prepare_paths(name, args, self.policy)

    def test_search_and_list_filter(self):
        with patch("nexus.tools.AccessPolicy.load", return_value=self.policy):
            self.assertEqual(search_files(str(self.allowed), "*.txt"), [str(self.source)])
            self.assertEqual(list_directory(str(self.allowed)), [str(self.source)])

    def test_config_roundtrip_and_corruption(self):
        config = self.root / "permissions.json"
        self.policy.save(config)
        self.assertEqual(AccessPolicy.load(config).rules, self.policy.rules)
        config.write_text("{")
        with self.assertRaises(PathDenied):
            AccessPolicy.load(config)

    def test_link_rejected(self):
        link = self.allowed / "alias"
        try:
            link.symlink_to(self.private, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with self.assertRaises(PathDenied):
            self.policy.check(link / "secret.txt")

    def test_blocked_before_action_confirmation(self):
        with patch("nexus.executor.AccessPolicy.load", return_value=self.policy), \
             patch("nexus.executor.confirm") as confirm, patch("nexus.executor._log"):
            result = execute_tool_call("delete_file", {"path": str(self.private / "secret.txt")})
            self.assertIn("Path blocked", result["error"])
            confirm.assert_not_called()

    def test_direct_permission_phrases(self):
        self.assertEqual(parse_permission("Restrict your access for D:/Finance"),
                         ("blocked", "D:/Finance"))
        self.assertEqual(parse_permission("Allow read-only access to D:/Finance"),
                         ("read_only", "D:/Finance"))
        self.assertIsNone(parse_permission("What if I block D:/Finance?"))

    def test_confirmation_required(self):
        with patch("nexus.permission_commands.AccessPolicy.load", return_value=self.policy), \
             patch.object(AccessPolicy, "save") as save:
            request = f"Restrict your access to {self.source}"
            handle_permission(request, ask=lambda _: "no", show=lambda _: None)
            save.assert_not_called()
            handle_permission(request, ask=lambda _: "CONFIRM", show=lambda _: None)
            save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
