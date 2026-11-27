"""Fast routing for a small, explicit subset of plain-English commands.

No filesystem access happens in the parser. Both routes call the same
executor, including argument validation, path policy and confirmation.
Anything ambiguous falls back to the AI planner.
"""
import re

from .executor import execute_tool_call

# A single explicit path. Spaces must be quoted; relative paths and
# implicit references (e.g. "that folder") are not fast-routed.
_PATH = r"""(?:"[^"\r\n]+"|'[^'\r\n]+'|[^\s"']+)"""
_EXPLICIT = re.compile(r"^(?:[A-Za-z]:[/\\]|~[/\\]|/)")
_NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
_PATTERNS = [
    (re.compile(rf"list (?:the )?contents of (?P<path>{_PATH})", re.I), "list_directory"),
    (re.compile(rf"list directory (?P<path>{_PATH})", re.I), "list_directory"),
    (re.compile(rf"create (?:a )?folder called (?P<name>{_NAME}) (?:in|inside) (?P<path>{_PATH})", re.I), "create_folder"),
    (re.compile(rf"create (?:a )?folder (?:at )?(?P<path>{_PATH})", re.I), "create_folder"),
    (re.compile(rf"(?:search recursively for|find recursively) (?P<pattern>\*\.[A-Za-z0-9]+) in (?P<path>{_PATH})", re.I), "search_files"),
]


def parse_fast_command(text):
    """Return (tool name, arguments), or None for the AI route."""
    for regex, tool in _PATTERNS:
        match = regex.fullmatch(text.strip())
        if not match:
            continue
        parts = match.groupdict()
        path = parts["path"]
        if path[0] in ("'", '"'):
            path = path[1:-1]
        if not _EXPLICIT.match(path) or any(ord(c) < 32 for c in path):
            return None
        if tool == "list_directory":
            return tool, {"directory": path}
        if tool == "search_files":
            return tool, {"directory": path, "pattern": parts["pattern"], "recursive": True}
        if parts.get("name"):
            path = path.rstrip("/\\") + "/" + parts["name"]
        return tool, {"path": path}
    return None


def run_fast_command(text):
    """Use the shared executor and report its actual result, without Ollama."""
    call = parse_fast_command(text)
    if call is None:
        return None
    tool, args = call
    outcome = execute_tool_call(tool, args)
    if "error" in outcome:
        return str(outcome["error"])
    result = outcome.get("result")
    if isinstance(result, list):
        if not result:
            return "No accessible matching entries."
        return f"{len(result)} accessible entries:\n" + "\n".join(repr(p) for p in result)
    if tool == "create_folder":
        return f"Folder ready: {result!r}"
    return str(result)
