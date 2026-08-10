"""
tools.py — The fixed, whitelisted set of actions NEXUS can take.

CRITICAL DESIGN RULE: the AI never generates or runs raw shell commands.
It can only ever request a call to one of these pre-written, tested
functions, with typed arguments. This file is the entire "surface area"
of what NEXUS is capable of doing to the filesystem — keep it small and
review every addition carefully.
"""

import os
import shutil
from pathlib import Path
from datetime import datetime

from .permissions import RiskTier


# ---------------------------------------------------------------------------
# Each tool is registered with: the function, its risk tier, and a JSON-schema
# description used for Claude's tool-use / function-calling API.
# ---------------------------------------------------------------------------

def search_files(directory: str, pattern: str, recursive: bool = True) -> list[str]:
    """Search for files matching a glob pattern (e.g. '*.pdf') under a directory."""
    base = Path(directory).expanduser()
    if not base.exists():
        return []
    matches = base.rglob(pattern) if recursive else base.glob(pattern)
    return [str(p) for p in matches if p.is_file()]


def list_directory(directory: str) -> list[str]:
    """List the contents of a directory (non-recursive)."""
    base = Path(directory).expanduser()
    if not base.exists():
        return []
    return [str(p) for p in base.iterdir()]


def create_folder(path: str) -> str:
    """Create a folder (and any missing parent folders)."""
    p = Path(path).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def move_file(source: str, destination_folder: str) -> str:
    """Move a single file into a destination folder (folder must exist or will be created)."""
    src = Path(source).expanduser()
    dest_dir = Path(destination_folder).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.move(str(src), str(dest))
    return str(dest)


def copy_file(source: str, destination_folder: str) -> str:
    """Copy a single file into a destination folder."""
    src = Path(source).expanduser()
    dest_dir = Path(destination_folder).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.copy2(str(src), str(dest))
    return str(dest)


def rename_file(source: str, new_name: str) -> str:
    """Rename a file in place (keeps it in the same directory)."""
    src = Path(source).expanduser()
    dest = src.parent / new_name
    src.rename(dest)
    return str(dest)


def delete_file(path: str) -> str:
    """Delete a single file. DESTRUCTIVE — always requires explicit confirmation."""
    p = Path(path).expanduser()
    p.unlink()
    return f"Deleted {p}"


# ---------------------------------------------------------------------------
# Tool registry: name -> (function, risk tier, description for the LLM)
# ---------------------------------------------------------------------------

TOOL_REGISTRY = {
    "search_files": {
        "fn": search_files,
        "tier": RiskTier.READ_ONLY,
        "description": "Search for files matching a glob pattern under a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Directory to search under"},
                "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.pdf'"},
                "recursive": {"type": "boolean", "description": "Search subdirectories too", "default": True},
            },
            "required": ["directory", "pattern"],
        },
    },
    "list_directory": {
        "fn": list_directory,
        "tier": RiskTier.READ_ONLY,
        "description": "List the contents of a directory.",
        "input_schema": {
            "type": "object",
            "properties": {"directory": {"type": "string"}},
            "required": ["directory"],
        },
    },
    "create_folder": {
        "fn": create_folder,
        "tier": RiskTier.REVERSIBLE_WRITE,
        "description": "Create a new folder (including parent folders if needed).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    "move_file": {
        "fn": move_file,
        "tier": RiskTier.REVERSIBLE_WRITE,
        "description": "Move a file into a destination folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination_folder": {"type": "string"},
            },
            "required": ["source", "destination_folder"],
        },
    },
    "copy_file": {
        "fn": copy_file,
        "tier": RiskTier.REVERSIBLE_WRITE,
        "description": "Copy a file into a destination folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination_folder": {"type": "string"},
            },
            "required": ["source", "destination_folder"],
        },
    },
    "rename_file": {
        "fn": rename_file,
        "tier": RiskTier.REVERSIBLE_WRITE,
        "description": "Rename a file in place.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "new_name": {"type": "string"},
            },
            "required": ["source", "new_name"],
        },
    },
    "delete_file": {
        "fn": delete_file,
        "tier": RiskTier.DESTRUCTIVE,
        "description": "Permanently delete a single file. Use with caution.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}


def claude_tool_schemas() -> list[dict]:
    """Build the tool-use schema list in Anthropic's format (kept for reference /
    in case you switch back to a paid API later)."""
    return [
        {
            "name": name,
            "description": entry["description"],
            "input_schema": entry["input_schema"],
        }
        for name, entry in TOOL_REGISTRY.items()
    ]


def ollama_tool_schemas() -> list[dict]:
    """Build the tool-use schema list in OpenAI/Ollama function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": entry["description"],
                "parameters": entry["input_schema"],
            },
        }
        for name, entry in TOOL_REGISTRY.items()
    ]
