"""
permissions.py — The permission / safety layer for NEXUS.

Every tool the AI can call is tagged with a risk tier. This module decides
whether an action can run automatically, needs a one-click confirmation,
needs an explicit typed confirmation, or is blocked outright.

This is the part of NEXUS that should never be bypassed — the LLM never
executes anything directly, it only ever *proposes* a call to a tool
function, and this layer decides what happens next.
"""

from enum import Enum


class RiskTier(Enum):
    READ_ONLY = "read_only"          # e.g. search, list, read — auto-run
    REVERSIBLE_WRITE = "reversible"  # e.g. move, copy, rename, create folder — confirm
    DESTRUCTIVE = "destructive"      # e.g. delete, overwrite — explicit typed confirm
    BLOCKED = "blocked"              # e.g. raw shell exec, system file edits — never allowed


class PermissionDenied(Exception):
    pass


def describe_plan(action_name: str, args: dict) -> str:
    """Turn a proposed tool call into a plain-language description for the user."""
    arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return f"{action_name}({arg_str})"


def confirm(action_name: str, args: dict, tier: RiskTier) -> bool:
    """
    Ask the user for confirmation appropriate to the risk tier.
    Returns True if the action is approved, False otherwise.
    Raises PermissionDenied if the tier is BLOCKED.
    """
    plan_desc = describe_plan(action_name, args)

    if tier == RiskTier.BLOCKED:
        raise PermissionDenied(
            f"Action '{action_name}' is blocked by policy and cannot be executed: {plan_desc}"
        )

    if tier == RiskTier.READ_ONLY:
        # Auto-run, but still tell the user what happened.
        print(f"[auto] {plan_desc}")
        return True

    if tier == RiskTier.REVERSIBLE_WRITE:
        print(f"\nNEXUS wants to: {plan_desc}")
        resp = input("Proceed? [y/N]: ").strip().lower()
        return resp == "y"

    if tier == RiskTier.DESTRUCTIVE:
        print(f"\n⚠️  DESTRUCTIVE ACTION: {plan_desc}")
        print("This cannot be easily undone.")
        resp = input(f"Type the action name '{action_name}' to confirm, or anything else to cancel: ").strip()
        return resp == action_name

    return False
