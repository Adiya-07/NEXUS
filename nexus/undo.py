"""Persistent, user-only undo for successful single-file operations."""
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .path_policy import AccessPolicy, PathDenied, safe_path
from .recovery import recoverable_delete

UNDO_ROOT = Path.home() / ".nexus" / "undo"
SUPPORTED = {"move_file", "rename_file", "copy_file"}


def _fingerprint(path):
    path = safe_path(path)
    def identity(info):
        return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                info.st_ctime_ns, stat.S_IMODE(info.st_mode)]
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PathDenied("Undo requires a regular, unlinked file.")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if identity(before) != identity(after) or identity(after) != identity(safe_path(path).stat()):
        raise PathDenied("File changed during fingerprinting.")
    return {"identity": identity(after), "sha256": digest.hexdigest()}


def _root(create=False):
    root = safe_path(UNDO_ROOT)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return safe_path(root)


def _record_path(undo_id):
    if not re.fullmatch(r"[0-9a-f]{32}", undo_id):
        raise PathDenied("Use an ID from 'undo list'.")
    return safe_path(_root() / (undo_id + ".json"))


def _save(path, data):
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, safe_path(path))
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def _load(undo_id):
    data = json.loads(_record_path(undo_id).read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("version") != 1
            or data.get("id") != undo_id or data.get("action") not in SUPPORTED
            or data.get("state") not in {"ready", "pending", "done", "uncertain"}
            or not all(isinstance(data.get(k), str) for k in ("source", "destination", "created_at"))
            or not isinstance(data.get("fingerprint"), dict)):
        raise PathDenied("Invalid undo record.")
    return data


def capture_before(name, args):
    if name not in SUPPORTED:
        return None
    source = Path(args["source"])
    dest = (source.parent / args["new_name"] if name == "rename_file"
            else Path(args["destination_folder"]) / source.name)
    return {"source": str(source), "destination": str(dest),
            "before": _fingerprint(source)}


def record_success(name, snapshot, result):
    if snapshot is None:
        return None
    source = safe_path(snapshot["source"])
    dest = safe_path(snapshot["destination"])
    if safe_path(result) != dest:
        raise PathDenied("Unexpected destination; undo not recorded.")
    fingerprint = _fingerprint(dest)
    before = snapshot["before"]
    if fingerprint["sha256"] != before["sha256"]:
        raise PathDenied("File contents changed; undo not recorded.")
    if name == "copy_file":
        if _fingerprint(source) != before:
            raise PathDenied("Original changed; undo not recorded.")
    else:
        if source.exists() or fingerprint["identity"][:2] != before["identity"][:2]:
            raise PathDenied("Move was not a same-volume rename; undo not recorded.")
    undo_id = uuid.uuid4().hex
    _root(create=True)
    data = {"version": 1, "id": undo_id, "action": name,
            "source": str(source), "destination": str(dest),
            "fingerprint": fingerprint, "source_fingerprint": before,
            "created_at": datetime.now(timezone.utc).isoformat(), "state": "ready"}
    _save(_record_path(undo_id), data)
    return undo_id


def list_undo():
    root = _root()
    if not root.exists():
        return []
    records = []
    for path in root.iterdir():
        if not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
            continue
        try:
            data = _load(path.stem)
            records.append(data)
        except (OSError, ValueError, RuntimeError, TypeError):
            continue
    return sorted(records, key=lambda item: item["created_at"], reverse=True)


def _validate(data):
    if data["state"] != "ready":
        raise PathDenied("This undo record is not available.")
    policy = AccessPolicy.load()
    dest = policy.check(data["destination"], write=True)
    source = policy.check(data["source"], write=data["action"] != "copy_file")
    if source == dest or _fingerprint(dest) != data["fingerprint"]:
        raise PathDenied("Destination has changed; undo refused.")
    if data["action"] == "copy_file":
        if _fingerprint(source) != data.get("source_fingerprint"):
            raise PathDenied("Original copy has changed or is missing; undo refused.")
    else:
        if source.exists() or not source.parent.is_dir():
            raise PathDenied("Original location is occupied or its folder is missing.")
        if dest.stat().st_dev != source.parent.stat().st_dev:
            raise PathDenied("Cross-volume undo is not supported.")
    return source, dest


def undo_action(undo_id, ask=input, show=print):
    data = _load(undo_id)
    source, dest = _validate(data)
    if data["action"] == "copy_file":
        show(f"Undo copy: move {str(dest)!r} to the recycle bin (retention applies).")
    else:
        show(f"Move {str(dest)!r} back to {str(source)!r}.")
    if ask("Type UNDO to confirm: ").strip() != "UNDO":
        return "Undo cancelled."
    if _load(undo_id) != data or _validate(data) != (source, dest):
        raise PathDenied("Undo record changed after confirmation.")
    record = _record_path(undo_id)
    data["state"] = "pending"
    _save(record, data)
    try:
        # Recheck immediately before touching the filesystem.
        _validate(dict(data, state="ready"))
        if data["action"] == "copy_file":
            result = recoverable_delete(str(dest))
        elif os.name == "nt":
            os.rename(dest, source)  # Windows refuses an existing target.
            result = f"Moved back to {str(source)!r}."
        else:
            # Atomic no-replace publication. If unlink fails, keep both links.
            os.link(dest, source)
            dest.unlink()
            result = f"Moved back to {str(source)!r}."
    except BaseException:
        data["state"] = "uncertain"
        _save(record, data)
        raise
    data["state"] = "done"
    _save(record, data)
    return result


def handle_undo(text, ask=input, show=print):
    command = text.strip().lower()
    if command not in {"undo", "undo list"} and not command.startswith("undo "):
        return False
    try:
        if command == "undo list":
            records = list_undo()
            if not records:
                show("No undo history.")
            for data in records:
                show(f"{data['id']} | {data['state']} | {data['action']} | {data['destination']!r}")
        elif command == "undo":
            records = [data for data in list_undo() if data["state"] == "ready"]
            if not records:
                show("No available undo actions.")
            else:
                show(undo_action(records[0]["id"], ask=ask, show=show))
        else:
            show(undo_action(command.split(maxsplit=1)[1], ask=ask, show=show))
    except (OSError, ValueError, RuntimeError, TypeError, EOFError, KeyboardInterrupt) as exc:
        show(f"Undo refused or failed: {exc}")
    return True
