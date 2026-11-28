import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus.language import normalize_read_command, parse_file_intent, run_file_intent
from nexus.path_policy import AccessPolicy, PathDenied


class LanguageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.file = self.root / "notes.txt"
        self.file.write_text("notes")
        self.policy = AccessPolicy({str(self.root): "read_write"})

    def parse(self, text):
        return parse_file_intent(text, policy=self.policy, workspace=self.root)

    def test_read_aliases(self):
        for text in ("recyle bin", "SHOW DELETED FILES", "show  recycle bin"):
            self.assertEqual(normalize_read_command(text), "recycle bin")
        for text in ("empty binn", "undoo", "delete notes.txt", "UNDO"):
            self.assertEqual(normalize_read_command(text), text)

    def test_short_rename(self):
        self.assertEqual(self.parse("rename notes.txt to study.txt"),
                         ("rename_file", {"source": str(self.file), "new_name": "study.txt"}))

    def test_explicit_rename(self):
        self.assertEqual(self.parse(f'rename "{self.file}" to "study notes.txt"'),
                         ("rename_file", {"source": str(self.file), "new_name": "study notes.txt"}))

    def test_copy_and_move(self):
        for verb in ("copy", "move"):
            self.assertEqual(self.parse(f"{verb} notes.txt to backup"),
                             (verb + "_file", {"source": str(self.file),
                                               "destination_folder": str(self.root / "backup")}))

    def test_missing_source_is_not_guessed(self):
        with self.assertRaises(PathDenied):
            self.parse("rename notes to study.txt")

    def test_blocked_source(self):
        self.policy.rules[str(self.file)] = "blocked"
        with self.assertRaises(PathDenied):
            self.parse("rename notes.txt to study.txt")

    def test_relative_traversal_refused(self):
        with self.assertRaises(PathDenied):
            self.parse("move notes.txt to ../outside")

    def test_ambiguous_commands_not_fast_routed(self):
        for text in ("rename that file", "move everything to backup",
                     "rename notes.txt to x.txt and delete it", "renmae notes.txt to x.txt"):
            self.assertIsNone(self.parse(text))

    def test_shared_executor(self):
        with patch("nexus.language.AccessPolicy.load", return_value=self.policy), \
             patch("nexus.language.DEFAULT_WORKSPACE", self.root), \
             patch("nexus.language.execute_tool_call", return_value={"error": "User declined"}) as run:
            self.assertEqual(run_file_intent("rename notes.txt to study.txt"), "User declined")
            run.assert_called_once_with("rename_file", {"source": str(self.file), "new_name": "study.txt"})

    def test_missing_file_never_calls_executor(self):
        with patch("nexus.language.AccessPolicy.load", return_value=self.policy), \
             patch("nexus.language.DEFAULT_WORKSPACE", self.root), \
             patch("nexus.language.execute_tool_call") as run:
            self.assertIn("File not found", run_file_intent("rename missing.txt to study.txt"))
            run.assert_not_called()
