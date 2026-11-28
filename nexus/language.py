"""Conservative local language routing for explicit single-file requests.

Only recognized, complete grammar is executed. No fuzzy mutation commands,
recursive filename guesses, or permission changes.
"""
import re
from pathlib import Path

from .path_policy import AccessPolicy, DEFAULT_WORKSPACE, PathDenied
from .executor import execute_tool_call

READ_ALIASES = {
    "recyle bin": "recycle bin",
    "recy cycle bin": "recycle bin",
    "show recycle bin": "recycle bin",
    "show deleted files": "recycle bin",
    "list deleted files": "recycle bin",
    "show undo history": "undo list",
    "show permissions": "permissions",
}

_TOKEN = r"""(?:"[^"\r\n]+"|'[^'\r\n]+'|[^\s"']+)"""
_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[/\\]|~[/\\]|/)")
_SIMPLE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_PATTERN = re.compile(
    rf"(?P<verb>rename|move|copy)(?: file)? (?P<src>{_TOKEN}) (?:to|as) (?P<dest>{_TOKEN})",
    re.IGNORECASE,
)


def normalize_read_command(text):
    """Exact read-only aliases; never normalize confirmation tokens or paths."""
    return READ_ALIASES.get(" ".join(text.lower().split()), text)


def _unquote(token):
    return token[1:-1] if token[0] in ("'", '"') else token


def _resolve(token, policy, write, workspace):
    value = _unquote(token)
    if _ABSOLUTE.match(value):
        return policy.check(value, write=write)
    # Bare names resolve only directly inside the configured default workspace.
    # No implicit search across drives, no guessing extensions.
    if not _SIMPLE.fullmatch(value) or value in {".", ".."}:
        raise PathDenied("Use a filename in the default workspace or an explicit absolute path.")
    base = policy.check(workspace)
    return policy.check(base / value, write=write)


def parse_file_intent(text, policy=None, workspace=None):
    match = _PATTERN.fullmatch(text.strip())
    if not match:
        return None
    verb, src, dest = match.group("verb", "src", "dest")
    verb = verb.lower()
    policy = AccessPolicy.load() if policy is None else policy
    workspace = DEFAULT_WORKSPACE if workspace is None else workspace
    source = _resolve(src, policy, verb != "copy", workspace)
    if not source.is_file():
        raise PathDenied(f"File not found: {str(source)!r}. No action taken.")
    if verb == "rename":
        name = _unquote(dest)
        # The common executor additionally validates the new filename.
        return "rename_file", {"source": str(source), "new_name": name}
    folder = _resolve(dest, policy, True, workspace)
    return verb + "_file", {"source": str(source), "destination_folder": str(folder)}


def run_file_intent(text):
    try:
        call = parse_file_intent(text)
    except (PathDenied, OSError) as exc:
        return f"Path blocked or unavailable: {exc}"
    if call is None:
        return None
    outcome = execute_tool_call(*call)
    return str(outcome.get("error", outcome.get("result")))
