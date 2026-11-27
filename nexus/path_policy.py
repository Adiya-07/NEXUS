"""Application-level path policy. Never trust model-supplied paths."""
import json
import os
import stat
import tempfile
from pathlib import Path

MODES = {"read_write", "read_only", "blocked"}
CONFIG_PATH = Path.home() / ".nexus" / "permissions.json"
DEFAULT_WORKSPACE = Path.home() / "nexus_test"


class PathDenied(ValueError):
    pass


def within(path, root):
    return path == root or root in path.parents


def safe_path(value):
    """Reject ambiguous paths, links/reparse points and special files."""
    try:
        if not isinstance(value, (str, Path)):
            raise PathDenied("Path must be a string.")
        raw = str(value)
        if not raw.strip() or any(ord(c) < 32 for c in raw):
            raise PathDenied("Invalid path.")
        p = Path(raw).expanduser()
        if not p.is_absolute() or ".." in p.parts:
            raise PathDenied("Use an absolute path or ~/path, without '..'.")
        if os.name == "nt":
            if p.drive.startswith("\\\\") or p.drive.startswith("//"):
                raise PathDenied("Network and device paths are not supported.")
            for part in p.parts[1:]:
                if (part.endswith((" ", ".")) or Path(part).is_reserved()
                        or any(c in '<>:"|?*' for c in part)):
                    raise PathDenied("Invalid or reserved Windows path component.")
        for part in (*reversed(p.parents), p):
            try:
                info = part.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISLNK(info.st_mode) or
                    getattr(info, "st_file_attributes", 0) & 0x400):
                raise PathDenied("Links, junctions and other reparse points are not allowed.")
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise PathDenied("Special files are not supported.")
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                raise PathDenied("Hard-linked files are not supported.")
        return p.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, PathDenied):
            raise
        raise PathDenied("Cannot safely resolve path.") from exc


def system_roots(path):
    home = Path.home()
    roots = [
        home / ".nexus", home / ".ssh", home / ".gnupg", home / ".aws",
        home / ".config", home / ".kube", home / ".azure", home / ".ollama",
        Path(__file__).resolve().parent.parent,
    ]
    if os.name == "nt":
        drive = Path(path.anchor)
        roots += [drive / name for name in (
            "Windows", "Program Files", "Program Files (x86)", "ProgramData",
            "System Volume Information", "$Recycle.Bin", "Recovery", "Boot",
            "pagefile.sys", "hiberfil.sys", "swapfile.sys",
        )]
        roots.append(home / "AppData")
        for key in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)",
                    "ProgramW6432", "ProgramData", "APPDATA", "LOCALAPPDATA"):
            if os.environ.get(key):
                roots.append(Path(os.environ[key]))
    else:
        roots += [Path(p) for p in (
            "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot",
            "/dev", "/proc", "/sys", "/root", "/var", "/System", "/Library",
        )]
    return [p.resolve(strict=False) for p in roots]


class AccessPolicy:
    def __init__(self, rules):
        self.rules = dict(rules)

    @classmethod
    def load(cls, config=None):
        config = CONFIG_PATH if config is None else Path(config)
        config = safe_path(config)
        try:
            raw = json.loads(config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls({str(safe_path(DEFAULT_WORKSPACE)): "read_write"})
        except (OSError, ValueError) as exc:
            raise PathDenied("Permission configuration cannot be read; refusing access.") from exc
        if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("rules"), dict):
            raise PathDenied("Invalid permission configuration; refusing access.")
        rules = {}
        for key, mode in raw["rules"].items():
            if not isinstance(mode, str) or mode not in MODES or not Path(key).is_absolute():
                raise PathDenied("Invalid permission rule; refusing access.")
            # Rules are stored canonical, but do not follow a newly created link.
            rules[str(Path(key))] = mode
        return cls(rules)

    def check(self, value, write=False):
        p = safe_path(value)
        if p == Path(p.anchor) or any(within(p, root) for root in system_roots(p)):
            raise PathDenied("System or NEXUS configuration location is protected.")
        modes = {mode for root, mode in self.rules.items() if within(p, Path(root))}
        if "blocked" in modes:
            raise PathDenied("User restriction blocks this path.")
        if write and "read_only" in modes:
            raise PathDenied("This path is read-only.")
        if not modes.intersection({"read_only", "read_write"}):
            raise PathDenied("Path is not approved. Ask the user to grant access.")
        return p

    def visible(self, value):
        try:
            self.check(value)
            return True
        except PathDenied:
            return False

    def preview_change(self, value, mode):
        if mode not in MODES | {"remove"}:
            raise PathDenied("Unknown access level.")
        p = safe_path(value)
        if p == Path(p.anchor) or any(within(p, root) for root in system_roots(p)):
            raise PathDenied("System and NEXUS locations cannot be changed.")
        if mode in {"read_write", "read_only"} and not p.exists() and p != DEFAULT_WORKSPACE.resolve():
            raise PathDenied("Grant access to an existing file or folder.")
        if mode == "remove" and not any(Path(key) == p for key in self.rules):
            raise PathDenied("No explicit rule at this path.")
        return p

    def changed(self, path, mode):
        rules = dict(self.rules)
        path = str(path)
        # Path equality is case-insensitive on Windows.
        for key in list(rules):
            if Path(key) == Path(path):
                del rules[key]
        if mode != "remove":
            rules[path] = mode
        return AccessPolicy(rules)

    def save(self, config=None):
        config = safe_path(CONFIG_PATH if config is None else Path(config))
        config.parent.mkdir(parents=True, exist_ok=True)
        # Replace atomically so a failed write does not truncate the old policy.
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=config.parent,
                                             delete=False) as handle:
                temp = Path(handle.name)
                json.dump({"version": 1, "rules": self.rules}, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, config)
        finally:
            if temp is not None and temp.exists():
                temp.unlink()


def _directory(path):
    if not path.is_dir():
        raise PathDenied("An existing directory is required.")


def _file(path):
    if not path.is_file():
        raise PathDenied("An existing regular file is required.")


def _new_target(path):
    if path.exists():
        raise PathDenied("Destination already exists; overwriting is not allowed.")


def _creation_parents(policy, path):
    for parent in path.parents:
        if parent.exists():
            _directory(parent)
            break
        policy.check(parent, write=True)


def prepare_paths(name, args, policy):
    """Return canonical arguments only after validating every affected path."""
    result = dict(args)
    if name in {"search_files", "list_directory"}:
        p = policy.check(args["directory"])
        _directory(p)
        result["directory"] = str(p)
        if name == "search_files":
            pattern = args["pattern"]
            if any(c in pattern for c in "/\\:") or pattern in {".", "..", "**"}:
                raise PathDenied("Use a filename pattern only, e.g. *.pdf.")
    elif name == "create_folder":
        p = policy.check(args["path"], write=True)
        if p.exists():
            _directory(p)
        _creation_parents(policy, p)
        result["path"] = str(p)
    elif name == "delete_file":
        p = policy.check(args["path"], write=True)
        _file(p)
        result["path"] = str(p)
    elif name in {"copy_file", "move_file", "rename_file"}:
        src = policy.check(args["source"], write=name != "copy_file")
        _file(src)
        result["source"] = str(src)
        if name == "rename_file":
            dest = policy.check(src.parent / args["new_name"], write=True)
        else:
            folder = policy.check(args["destination_folder"], write=True)
            if folder.exists():
                _directory(folder)
            _creation_parents(policy, folder)
            result["destination_folder"] = str(folder)
            dest = policy.check(folder / src.name, write=True)
        _new_target(dest)
    else:
        raise PathDenied("No path policy implemented for this tool.")
    return result
