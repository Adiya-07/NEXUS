"""
executor.py — Takes a proposed tool call, sends it through the permission
layer, executes it if approved, and logs every action (approved or not)
for the history/undo view.
"""

import json
from datetime import datetime
from pathlib import Path

from .tools import TOOL_REGISTRY
from .permissions import confirm, PermissionDenied, describe_plan

LOG_PATH = Path.home() / ".nexus" / "history.jsonl"


def _log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def execute_tool_call(name: str, args: dict) -> dict:
    """
    Execute a single proposed tool call after passing it through the
    permission layer. Returns a dict describing the outcome — this is what
    gets sent back to Claude as the tool_result.
    """
    if name not in TOOL_REGISTRY:
        return {"error": f"Unknown tool '{name}' — refusing to execute."}

    entry = TOOL_REGISTRY[name]
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
