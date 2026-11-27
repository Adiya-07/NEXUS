"""Regression tests for deterministic routing and the shared security gate."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus.fast_path import parse_fast_command, run_fast_command
from nexus.path_policy import AccessPolicy


class FastPathTests(unittest.TestCase):
    def test_list_and_search(self):
        self.assertEqual(parse_fast_command("List the contents of C:/Windows"),
                         ("list_directory", {"directory": "C:/Windows"}))
        self.assertEqual(parse_fast_command('List directory "~/My Folder"'),
                         ("list_directory", {"directory": "~/My Folder"}))
        self.assertEqual(parse_fast_command("Find recursively *.pdf in ~/nexus_test"),
                         ("search_files", {"directory": "~/nexus_test",
                                           "pattern": "*.pdf", "recursive": True}))

    def test_create(self):
        self.assertEqual(parse_fast_command("Create a folder called Demo inside ~/nexus_test"),
                         ("create_folder", {"path": "~/nexus_test/Demo"}))

    def test_ambiguous_or_unsupported_falls_back(self):
        for text in ("list that folder", "list directory relative/path",
                     "list directory ~/nexus_test and delete it",
                     "delete file ~/nexus_test/a.txt",
                     "What files can you access?", "permissions"):
            with self.subTest(text=text), patch("nexus.fast_path.execute_tool_call") as execute:
                self.assertIsNone(run_fast_command(text))
                execute.assert_not_called()

    def test_executor_result_not_ai_claim(self):
        with patch("nexus.fast_path.execute_tool_call", return_value={"error": "Path blocked"}) as execute:
            self.assertEqual(run_fast_command("List directory C:/Windows"), "Path blocked")
            execute.assert_called_once_with("list_directory", {"directory": "C:/Windows"})

    def test_real_security_gate(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp).resolve()
            private = root / "private"
            private.mkdir()
            policy = AccessPolicy({str(root): "read_write", str(private): "blocked"})
            with patch("nexus.executor.AccessPolicy.load", return_value=policy), \
                 patch("nexus.executor._log") as log, patch("nexus.executor.confirm") as confirm:
                result = run_fast_command(f'List directory "{private}"')
                self.assertIn("Path blocked", result)
                self.assertEqual(log.call_args.args[0]["status"], "blocked_path")
                confirm.assert_not_called()

    def test_write_still_requires_confirmation(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp).resolve()
            target = root / "new"
            policy = AccessPolicy({str(root): "read_write"})
            with patch("nexus.executor.AccessPolicy.load", return_value=policy), \
                 patch("nexus.executor._log"), patch("nexus.executor.confirm", return_value=False) as confirm:
                self.assertIn("declined", run_fast_command(f'Create folder "{target}"'))
                confirm.assert_called_once()
                self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
