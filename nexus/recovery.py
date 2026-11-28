"""Protected recycle bin with user-only restore, retention and pinning."""
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .path_policy import AccessPolicy, PathDenied, safe_path

RECOVERY_ROOT = Path.home() / ".nexus" / "recovery"
DEFAULT_RETENTION = 7


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
    # Legacy entries have no expiry and are kept indefinitely.
    if "pinned" in data and type(data["pinned"]) is not bool:
        raise PathDenied("Invalid pin status.")
    if data.get("expires_at") is not None:
        _date(data["expires_at"])
    return data


def _now():
    return datetime.now(timezone.utc)


def _date(value):
    if not isinstance(value, str):
        raise PathDenied("Invalid expiry.")
    date = datetime.fromisoformat(value)
    if date.tzinfo is None:
        raise PathDenied("Missing expiry timezone.")
    return date


def _pinned(data):
    return data.get("pinned", "expires_at" not in data)


def _atomic_json(path, data):
    path = safe_path(path)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, safe_path(path))
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def retention_days():
    try:
        data = json.loads(safe_path(_root() / "settings.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_RETENTION
    if (not isinstance(data, dict) or type(data.get("days")) is not int
            or not 1 <= data["days"] <= 365):
        raise PathDenied("Invalid recycle bin retention settings.")
    return data["days"]


def recoverable_delete(path):
    """Move the original file into a unique, protected recovery directory."""
    source = AccessPolicy.load().check(path, write=True)
    if not source.is_file():
        raise PathDenied("Only regular files can be recovered.")
    root = _root(create=True)
    if source.stat().st_dev != root.stat().st_dev:
        raise PathDenied("Recovery storage is on another volume; original left unchanged.")
    now = _now()
    expires = now + timedelta(days=retention_days())
    recovery_id = uuid.uuid4().hex
    entry = root / recovery_id
    entry.mkdir(mode=0o700)
    # Write metadata before moving: a crash after rename still leaves a record.
    with (entry / "metadata.json").open("x", encoding="utf-8") as handle:
        json.dump({"version": 1, "original": str(source),
                   "deleted_at": now.isoformat(), "expires_at": expires.isoformat(),
                   "pinned": False}, handle)
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


def set_pinned(recovery_id, pinned):
    entry = _entry(recovery_id)
    data = _metadata(entry)
    if not safe_path(entry / "payload").is_file():
        raise PathDenied("Recovery payload is missing.")
    data["pinned"] = pinned
    data["expires_at"] = None if pinned else (_now() + timedelta(days=retention_days())).isoformat()
    _atomic_json(entry / "metadata.json", data)
    return "File kept indefinitely." if pinned else "File unpinned; new retention period begins."


def _purge(recovery_id, expected, reason):
    # Only the known regular payload is removed; never recursively delete.
    entry = _entry(recovery_id)
    data = _metadata(entry)
    if data != expected or _pinned(data):
        raise PathDenied("Recovery entry changed or is pinned.")
    payload = safe_path(entry / "payload")
    if not payload.is_file():
        raise PathDenied("Recovery payload is missing.")
    if reason == "expired" and (not data.get("expires_at") or _date(data["expires_at"]) > _now()):
        raise PathDenied("Entry is not expired.")
    payload.unlink()
    data["purged_at"] = _now().isoformat()
    data["purge_reason"] = reason
    _atomic_json(entry / "metadata.json", data)


def cleanup_expired(show=print):
    try:
        retention_days()
        for recovery_id, data in list_recovery():
            if _pinned(data) or not data.get("expires_at"):
                continue
            try:
                if _date(data["expires_at"]) <= _now():
                    _purge(recovery_id, data, "expired")
                    show(f"Recycle bin: expired {recovery_id}.")
            except (OSError, ValueError, RuntimeError) as exc:
                show(f"Recycle bin cleanup skipped {recovery_id}: {exc}")
    except (OSError, ValueError, RuntimeError) as exc:
        show(f"Recycle bin cleanup unavailable: {exc}")


def handle_recovery(text, ask=input, show=print):
    """Direct user CLI only; none of these commands are model tools."""
    command = text.strip().lower()
    listing = command in {"recovery", "list recovery", "recycle bin"}
    match = re.fullmatch(r"(restore|keep|unkeep)\s+([0-9a-f]{32})", command)
    retention = re.fullmatch(r"retention(?:\s+(\d{1,3}))?", command)
    empty = command == "empty bin"
    if not (listing or match or retention or empty):
        return False
    try:
        if listing:
            records = list_recovery()
            if not records:
                show("No recoverable files.")
            total = 0
            for recovery_id, data in records:
                size = safe_path(_entry(recovery_id) / "payload").stat().st_size
                total += size
                expiry = "kept indefinitely" if _pinned(data) else data.get("expires_at", "no expiry")
                show(f"{recovery_id} | {data['original']!r} | {size} bytes | {expiry}")
            show(f"Recycle bin: {total} bytes | New file retention: {retention_days()} days")
        elif match:
            action, recovery_id = match.groups()
            if action == "restore":
                show(restore_file(recovery_id, ask=ask, show=show))
            else:
                if action == "unkeep":
                    show("Unpinning starts a new expiration period.")
                    if ask("Type UNKEEP to confirm: ").strip() != "UNKEEP":
                        show("Cancelled.")
                        return True
                show(set_pinned(recovery_id, action == "keep"))
        elif retention:
            if retention.group(1) is None:
                show(f"Retention for new files: {retention_days()} days")
            else:
                days = int(retention.group(1))
                if not 1 <= days <= 365:
                    raise PathDenied("Choose 1 to 365 days.")
                show(f"New deletions permanently expire after {days} days. Existing expiry dates are unchanged.")
                if ask("Type CONFIRM to apply: ").strip() == "CONFIRM":
                    _atomic_json(_root(create=True) / "settings.json", {"days": days})
                    show("Retention updated.")
                else:
                    show("Cancelled.")
        elif empty:
            candidates = [(i, d) for i, d in list_recovery() if not _pinned(d)]
            if not candidates:
                show("No unpinned files to empty.")
                return True
            for recovery_id, data in candidates:
                show(f"{recovery_id} | {data['original']!r}")
            if ask(f"Permanently delete {len(candidates)} unpinned files? Type EMPTY: ").strip() != "EMPTY":
                show("Cancelled.")
                return True
            for recovery_id, data in candidates:
                try:
                    _purge(recovery_id, data, "manual")
                    show(f"Purged {recovery_id}.")
                except (OSError, ValueError, RuntimeError) as exc:
                    show(f"Skipped {recovery_id}: {exc}")
    except (OSError, ValueError, RuntimeError, EOFError, KeyboardInterrupt) as exc:
        show(f"Recovery refused or failed: {exc}")
    return True
