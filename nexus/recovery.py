"""Recoverable single-file deletion and user-only restore.

The recovery directory is outside the agent's approved filesystem. No
permanent purge is exposed. Cross-volume deletion fails rather than
falling back to a risky copy-and-delete.
"""
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .path_policy import AccessPolicy, PathDenied, safe_path

RECOVERY_ROOT = Path.home() / ".nexus" / "recovery"


def _root(create=False):
    root = safe_path(RECOVERY_ROOT)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return safe_path(root)


def _entry(recovery_id):
    if not re.fullmatch(r"[0-9a-f]{32}", recovery_id):
        raise PathDenied("Use a valid recovery ID from 'recovery'.")
    entry = safe_path(_root() / recovery_id)
    if not entry.is_dir():
        raise PathDenied("Recovery entry not found.")
    return entry


def _metadata(entry):
    data = json.loads(safe_path(entry / "metadata.json").read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("original"), str)
            or not isinstance(data.get("deleted_at"), str)):
        raise PathDenied("Invalid recovery metadata.")
    return data


def recoverable_delete(path):
    """Move the original file into a unique, protected recovery directory."""
    source = AccessPolicy.load().check(path, write=True)
    if not source.is_file():
        raise PathDenied("Only regular files can be recovered.")
    root = _root(create=True)
    if source.stat().st_dev != root.stat().st_dev:
        raise PathDenied("Recovery storage is on another volume; original left unchanged.")
    recovery_id = uuid.uuid4().hex
    entry = root / recovery_id
    entry.mkdir(mode=0o700)
    # Write metadata before moving: a crash after rename still leaves a record.
    with (entry / "metadata.json").open("x", encoding="utf-8") as handle:
        json.dump({"version": 1, "original": str(source),
                   "deleted_at": datetime.now(timezone.utc).isoformat()}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    source = AccessPolicy.load().check(source, write=True)
    if not source.is_file():
        raise PathDenied("Source changed before recovery.")
    os.rename(source, entry / "payload")
    return f"Moved {str(source)!r} to recovery. ID: {recovery_id}"


def list_recovery():
    root = _root()
    if not root.exists():
        return []
    records = []
    for child in root.iterdir():
        if not re.fullmatch(r"[0-9a-f]{32}", child.name):
            continue
        try:
            entry = _entry(child.name)
            if not safe_path(entry / "payload").is_file():
                continue
            records.append((child.name, _metadata(entry)))
        except (OSError, ValueError, RuntimeError):
            continue
    return sorted(records, key=lambda item: item[1]["deleted_at"], reverse=True)


def _restore_target(data):
    target = AccessPolicy.load().check(data["original"], write=True)
    if target.exists():
        raise PathDenied("Original location already exists; nothing was overwritten.")
    if not target.parent.is_dir():
        raise PathDenied("Original parent folder is missing; recreate it first.")
    return target


def restore_file(recovery_id, ask=input, show=print):
    """Restore a copy to the original location, keeping the backup intact."""
    entry = _entry(recovery_id)
    data = _metadata(entry)
    payload = safe_path(entry / "payload")
    if not payload.is_file():
        raise PathDenied("Recovery payload is missing.")
    target = _restore_target(data)
    show(f"Restore {str(target)!r} from recovery ID {recovery_id}?")
    if ask("Type RESTORE to confirm: ").strip() != "RESTORE":
        return "Restore cancelled."
    if _metadata(entry) != data or _restore_target(data) != target:
        raise PathDenied("Restore target changed after confirmation.")
    payload = safe_path(entry / "payload")
    # Stage a complete copy inside the private recovery entry. Publish
    # without replacing an existing destination. Keep the original backup.
    staging = None
    try:
        with tempfile.NamedTemporaryFile(dir=entry, delete=False) as out:
            staging = Path(out.name)
            with payload.open("rb") as source:
                shutil.copyfileobj(source, out)
            out.flush()
            os.fsync(out.fileno())
        shutil.copystat(payload, staging)
        if _restore_target(data) != target:
            raise PathDenied("Restore target changed.")
        if os.name == "nt":
            os.rename(staging, target)  # Windows refuses an existing target.
        else:
            os.link(staging, target)  # POSIX atomic no-replace publication.
        return f"Restored {str(target)!r}. Recovery backup retained."
    finally:
        if staging is not None and staging.exists():
            staging.unlink()


def handle_recovery(text, ask=input, show=print):
    """Only the direct user CLI calls this; not exposed to the model."""
    command = text.strip()
    listing = command.lower() in {"recovery", "list recovery"}
    match = re.fullmatch(r"restore\s+([0-9a-f]{32})", command, re.IGNORECASE)
    if not listing and not match:
        return False
    try:
        if listing:
            records = list_recovery()
            if not records:
                show("No recoverable files.")
            for recovery_id, data in records:
                show(f"{recovery_id} | {data['deleted_at']} | {data['original']!r}")
        else:
            show(restore_file(match.group(1).lower(), ask=ask, show=show))
    except (OSError, ValueError, RuntimeError, EOFError, KeyboardInterrupt) as exc:
        show(f"Recovery refused or failed: {exc}")
    return True
