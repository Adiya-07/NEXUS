"""
executor.py — Takes a proposed tool call, sends it through the permission
layer, executes it if approved, and logs every action (approved or not)
for the history/undo view.
"""

import json
from datetime import datetime
from pathlib import Path

from .tools import TOOL_REGISTRY
from .path_policy import AccessPolicy, PathDenied, prepare_paths
from .validation import ArgumentValidationError, validate_arguments
from .permissions import confirm, PermissionDenied, describe_plan

LOG_PATH = Path.home() / ".nexus" / "history.jsonl"


def _log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def execute_tool_call(name: str, args: object) -> dict:
    """
    Execute a single proposed tool call after passing it through the
    permission layer. Returns a dict describing the outcome — this is what
    gets sent back to Claude as the tool_result.
    """
    if not isinstance(name, str) or name not in TOOL_REGISTRY:
        return {"error": f"Unknown tool '{name}' — refusing to execute."}

    entry = TOOL_REGISTRY[name]
    try:
        args = validate_arguments(name, args, entry["input_schema"])
    except ArgumentValidationError as e:
        _log({
            "timestamp": datetime.now().isoformat(),
            "action": name,
            "status": "invalid_arguments",
            "error": str(e),
        })
        return {"error": f"Invalid arguments for {name}: {e}"}

    try:
        args = prepare_paths(name, args, AccessPolicy.load())
    except (PathDenied, OSError) as e:
        _log({"timestamp": datetime.now().isoformat(), "action": name,
              "status": "blocked_path", "reason": str(e)})
        return {"error": f"Path blocked: {e}"}

    tier = entry["tier"]
    plan_desc = describe_plan(name, args)

    try:
        approved = confirm(name, args, tier)
    except PermissionDenied as e:
        _log({
            "timestamp": datetime.now().isoformat(),
            "action": plan_desc,
            "status": "blocked",
            "reason": str(e),
        })
        return {"error": str(e)}

    if not approved:
        _log({
            "timestamp": datetime.now().isoformat(),
            "action": plan_desc,
            "status": "declined_by_user",
        })
        return {"error": "User declined this action."}

    try:
        # Recheck after waiting for user confirmation.
        args = prepare_paths(name, args, AccessPolicy.load())
        result = entry["fn"](**args)
        _log({
            "timestamp": datetime.now().isoformat(),
            "action": plan_desc,
            "status": "executed",
            "result": str(result),
        })
        return {"result": result}
    except Exception as e:
        _log({
            "timestamp": datetime.now().isoformat(),
            "action": plan_desc,
            "status": "error",
            "error": str(e),
        })
        return {"error": f"Execution failed: {e}"}


def print_history(limit: int = 20) -> None:
    """Print the most recent N logged actions."""
    if not LOG_PATH.exists():
        print("No history yet.")
        return
    lines = LOG_PATH.read_text().strip().splitlines()[-limit:]
    for line in lines:
        entry = json.loads(line)
        print(f"[{entry['timestamp']}] {entry['status']:>16} — {entry['action']}")
