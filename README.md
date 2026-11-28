# VEYL — Phase 1.5 (Hybrid File Assistant)

An AI-native way to operate your filesystem through natural language,
built around a strict permission/safety layer — the AI never runs raw
shell commands, it only ever proposes calls to a small, whitelisted set
of tested functions.

**Runs entirely locally via [Ollama](https://ollama.com) — free, unlimited,
no API key, no internet required after setup.** Tradeoff vs. a paid API:
local models are less reliable at multi-step tool-calling, so expect more
retries/rephrasing on complex requests than you'd get from Claude/GPT.

## Why this design

Most "AI controls your computer" demos let the model generate and execute
arbitrary shell commands. That's fast to build and dangerous to run. VEYL
takes the slower, safer path:

```
Natural language
      ↓
Command router → Python fast path OR Qwen2.5 3B via Ollama
      ↓
Argument validation + path policy (default-deny)
      ↓
Permission layer (risk-tiers every action: read-only / reversible / destructive / blocked)
      ↓
Whitelisted tool functions (move_file, search_files, create_folder, ...)
      ↓
Filesystem
```

Every action is risk-tiered:
- **Read-only** (search, list) — runs automatically
- **Reversible write** (move, copy, rename, create folder) — shown as a plan, needs a y/N confirm
- **Destructive** (delete) — requires typing the action name to confirm
- **Blocked** — anything not in the tool registry (e.g. raw shell exec, system file edits) is refused outright, no matter what the model asks for

Every action, approved or not, is logged to `~/.nexus/history.jsonl` for
a full audit trail.

## Setup

1. **Install Ollama**: https://ollama.com/download (macOS/Windows/Linux, free)
2. **Pull a tool-capable model** (pick one based on your laptop's RAM):
   ```bash
   ollama pull qwen2.5:3b    # default model
   # Optional: ollama pull qwen3:4b
   ```
3. **Install Python deps and run**:
   ```bash
   pip install -r requirements.txt
   python -m veyl.main
   ```

Ollama runs a local server automatically after install (`localhost:11434`) —
no need to start anything separately. If you want to use a different model
than the default, set `VEYL_MODEL`:
```bash
export VEYL_MODEL=qwen2.5:7b
python -m veyl.main
```

## Hybrid command routing

Simple commands with explicit paths can use Python directly, without AI
inference. Other requests go through the local AI planner, which receives
the host operating system and home-directory context. Both routes use
the same argument validation, path policy, confirmation and execution
logic.

Examples of fast-path commands:

- `List the contents of ~/nexus_test`
- `Create a folder called Demo inside ~/nexus_test`
- `Find recursively *.pdf in ~/nexus_test`

Quote paths containing spaces. The CLI displays which route was used and
elapsed time. Manual Windows smoke tests recorded 0.02s for a directory
listing, 0.01s for a blocked system path, and 13.68s for an AI response.
These are individual observations, not a controlled benchmark.

## Recycle bin (Phase 1.5)

Deletion requires typing `delete_file` at confirmation. The original file
moves into protected `~/.nexus/recovery` storage. New unpinned files expire
after seven days by default. Cleanup runs on startup and between terminal
requests when an hour has elapsed; it does not run while VEYL is closed
or idle at the input prompt.

Direct user commands (not available as AI tools):

- `recycle bin` or `recovery`: list file IDs, paths, sizes and expiration.
- `restore <ID>`: confirm with RESTORE; never overwrite existing files.
  The recovery copy is retained until expiry.
- `keep <ID>`: keep a file indefinitely.
- `unkeep <ID>`: confirm with UNKEEP to begin a fresh retention period.
- `retention` or `retention 14`: inspect or set retention (1–365 days,
  CONFIRM required; applies to new deletions and unpinning).
- `empty bin`: preview unpinned files and type EMPTY to permanently purge.

Legacy recovery records without expiry are kept indefinitely. Pinned files
are excluded from automatic and manual cleanup. Metadata remains after
purging. Cross-volume deletion is refused. Undo for moves, renames and copies is supported as described below. Recovery is not a substitute for independent backups.

## Undo engine

Direct user commands: `undo list`, `undo` (latest available record), or
`undo <ID>`. Type UNDO to confirm. The AI cannot invoke these commands.
Successful single-file moves, renames and copies are journaled in
`~/.nexus/undo`. SHA-256 and file identity/metadata checks detect changes;
current permissions are rechecked and existing destinations are never
overwritten by undo. Undoing a copy sends the duplicate to the recycle bin.
Cross-volume moves, folder creation, past unjournaled actions and records
left pending/uncertain by failures are not automatically reversible.
Hashing large files may take time. This is application-level protection,
not an OS sandbox or a transactional filesystem.

## Name and compatibility

VEYL is the new project name. Use `python -m veyl.main` to launch.
The existing `nexus` implementation package, `~/.nexus` state directory,
and `~/nexus_test` workspace are intentionally retained in this release
to preserve compatibility, permissions, history and recovery records.
The old NEXUS_MODEL environment variable is supported as a fallback to
VEYL_MODEL. No existing data is migrated or deleted by the rebrand.

## Access control

By default only ~/nexus_test is approved for read/write. VEYL creates it
on startup if permitted. Use absolute paths or paths starting with ~/.
All other paths are denied, including protected system and VEYL directories.

Type these direct terminal commands (English phrases supported by a small
rule-based parser, not arbitrary AI interpretation):

- permissions
- Allow access to D:/College
- Restrict your access to D:/College/Finance
- Set read-only access to D:/College/Notes
- Remove restriction for D:/College/Finance

Each change requires typing CONFIRM at a separate Python prompt.
Rules persist in ~/.nexus/permissions.json. Blocked and read-only rules
override more permissive rules, including inherited grants. Removing a
rule may restore inherited access. Grants require existing paths except the
default workspace. Search skips inaccessible descendants and links; write
operations check source and final destination and refuse existing targets.
The model cannot call the permission manager.

This is application-level protection, not an OS sandbox: concurrent
filesystem changes can race validation, and other programs running as
the user can edit the configuration. Do not grant broad sensitive locations.
Use a dedicated test workspace first.

Run regression tests with python -m unittest discover -s tests -v.

## Project structure

```
nexus/
  tools.py          — the whitelisted actions VEYL can take (the entire "surface area")
  permissions.py    — risk tiers + confirmation logic
  path_policy.py    — path checks and persistent access rules
  permission_commands.py — direct user permission requests
  validation.py     — argument validation
  fast_path.py      — deterministic command routing
  recovery.py       — protected recycle bin, retention and user-only restore
  undo.py           — persistent journal and user-only reversal
  ollama_planner.py — local Ollama tool-use integration (natural language → proposed plan)
  executor.py       — runs approved calls, logs everything to ~/.nexus/history.jsonl
  main.py           — CLI loop
```

**Note:** `tools.py` still includes `claude_tool_schemas()` alongside
`ollama_tool_schemas()` — if you ever get API credits or want higher
reliability for a demo, you can swap back to a Claude-based planner without
touching the tools or permission layer at all. That separation (tools/
permissions never depend on which model you use) is intentional.

## Roadmap

- **Phase 1 (this version):** file operations — search, move, copy, rename,
  create folder, delete. All permission-gated.
- **Phase 1.5 (in progress):** hybrid routing, performance measurement,
  reliability improvements, recycle bin, and planned undo for other operations.
- **Phase 2:** app control — open/close apps, basic window management, via
  OS-level APIs (no shell).
- **Phase 3 (highest risk, do last or skip):** a tightly whitelisted subset
  of read-only terminal commands (`ls`, `grep`, `git status`, etc.) with
  validated arguments — never free-form shell execution.

## Extending the tool registry

To add a new capability, add an entry to `TOOL_REGISTRY` in `tools.py` with:
1. A small, well-tested Python function
2. A `RiskTier`
3. A JSON-schema `input_schema` describing its arguments to the AI planner

Never add a tool that accepts a raw command string as an argument — that
defeats the entire point of the whitelist.
