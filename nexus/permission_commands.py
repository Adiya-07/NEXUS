"""Trusted CLI permission changes; not a filesystem tool or model capability."""
import re
from .path_policy import AccessPolicy, PathDenied

# Deliberately narrow natural-language grammar: only direct user input can
# reach this handler. Quoting a command inside a longer request is not enough.
COMMANDS = [
    (r"(?:restrict (?:your )?access (?:to|for)|block(?: access to)?|deny access to)\s+(.+)", "blocked"),
    (r"(?:set read[- ]only access (?:to|for)|make read[- ]only|allow read[- ]only access to)\s+(.+)", "read_only"),
    (r"(?:allow(?: access to)?|grant (?:read[- ]write )?access to|give yourself access to)\s+(.+)", "read_write"),
    (r"(?:remove (?:the )?(?:permission|restriction) (?:for|on|from)|unblock)\s+(.+)", "remove"),
]


def parse_permission(text):
    for pattern, mode in COMMANDS:
        match = re.fullmatch(pattern, text.strip(), flags=re.IGNORECASE)
        if match:
            path = match.group(1).strip()
            if len(path) >= 2 and path[0] == path[-1] and path[0] in "'\"":
                path = path[1:-1]
            return mode, path
    return None


def handle_permission(text, ask=input, show=print):
    """Return True if handled; never send permission edits to Ollama."""
    listing = text.strip().lower() in {"permissions", "show permissions", "list permissions"}
    request = parse_permission(text)
    if not listing and request is None:
        return False
    try:
        policy = AccessPolicy.load()
        if listing:
            for path, mode in sorted(policy.rules.items()):
                show(f"{mode:12} {path!r}")
            show("All other paths are denied; system protections are always active.")
            return True
        mode, path = request
        target = policy.preview_change(path, mode)
        show(f"Permission change: {mode} for {str(target)!r}")
        if mode == "remove":
            show("Removing a rule may restore access inherited from a parent.")
        if ask("Type CONFIRM to save, or anything else to cancel: ").strip() != "CONFIRM":
            show("Permission unchanged.")
            return True
        current = AccessPolicy.load()
        if current.preview_change(path, mode) != target:
            raise PathDenied("Target changed after confirmation.")
        current.changed(target, mode).save()
        show("Permission updated.")
    except (PathDenied, OSError, EOFError, KeyboardInterrupt) as exc:
        show(f"Permission change refused: {exc}")
    return True
