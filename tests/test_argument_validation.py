"""Regression tests for the argument-validation gate (no real file actions)."""

import unittest
from unittest.mock import patch

from nexus.executor import execute_tool_call
from nexus.tools import TOOL_REGISTRY
from nexus.validation import ArgumentValidationError, validate_arguments


class ValidationTests(unittest.TestCase):
    def validate(self, name, args):
        return validate_arguments(name, args, TOOL_REGISTRY[name]["input_schema"])

    def test_valid_calls_for_all_registered_tools(self):
        examples = {
            "search_files": {"directory": ".", "pattern": "*.pdf"},
            "list_directory": {"directory": "."},
            "create_folder": {"path": "College"},
            "copy_file": {"source": "a.txt", "destination_folder": "College"},
            "move_file": {"source": "a.txt", "destination_folder": "College"},
            "rename_file": {"source": "a.txt", "new_name": "b.txt"},
            "delete_file": {"path": "a.txt"},
        }
        self.assertEqual(set(examples), set(TOOL_REGISTRY))
        for name, args in examples.items():
            with self.subTest(name=name):
                self.assertEqual(self.validate(name, args), args)

    def test_valid_json_and_optional_boolean(self):
        self.assertEqual(
            self.validate("search_files", '{"directory":".","pattern":"*.pdf","recursive":false}'),
            {"directory": ".", "pattern": "*.pdf", "recursive": False},
        )

    def test_invalid_arguments(self):
        for name, args in [
            ("delete_file", {}),
            ("delete_file", {"path": "a.txt", "force": True}),
            ("delete_file", {"path": 123}),
            ("delete_file", {"path": None}),
            ("delete_file", {"path": "  "}),
            ("delete_file", {"path": "a\x00.txt"}),
            ("delete_file", []),
            ("delete_file", None),
            ("delete_file", '{"path":'),
            ("delete_file", '"a.txt"'),
            ("search_files", {"directory": ".", "pattern": "*", "recursive": "false"}),
            ("search_files", {"directory": ".", "pattern": "*", "recursive": 1}),
        ]:
            with self.subTest(name=name, args=args):
                with self.assertRaises(ArgumentValidationError):
                    self.validate(name, args)

    def test_rename_rejects_paths(self):
        for name in ("folder/b.txt", r"folder\b.txt", "../b.txt", r"C:\b.txt", "C:b.txt", ".", "..", "b.txt."):
            with self.subTest(name=name):
                with self.assertRaises(ArgumentValidationError):
                    self.validate("rename_file", {"source": "a.txt", "new_name": name})

    def test_invalid_call_never_reaches_permission_or_tool(self):
        with patch("nexus.executor.confirm") as confirm, patch("nexus.executor._log") as log:
            with patch.dict(TOOL_REGISTRY["delete_file"], {"fn": lambda **_: self.fail("Tool executed")}):
                result = execute_tool_call("delete_file", {"path": None})
            self.assertIn("Invalid arguments", result["error"])
            confirm.assert_not_called()
            self.assertEqual(log.call_args.args[0]["status"], "invalid_arguments")

    def test_valid_call_still_requires_permission(self):
        with patch("nexus.executor.confirm", return_value=False) as confirm, patch("nexus.executor._log"), patch("nexus.executor.AccessPolicy.load"), patch("nexus.executor.prepare_paths", side_effect=lambda name, args, policy: args):
            with patch.dict(TOOL_REGISTRY["delete_file"], {"fn": lambda **_: self.fail("Tool executed")}):
                result = execute_tool_call("delete_file", {"path": "a.txt"})
            confirm.assert_called_once()
            self.assertIn("declined", result["error"])

    def test_unknown_or_invalid_tool_name(self):
        for name in ("shell", None, []):
            with self.subTest(name=name):
                self.assertIn("error", execute_tool_call(name, {}))


if __name__ == "__main__":
    unittest.main()
