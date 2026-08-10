# NEXUS — Phase 1 (File Operations)

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
arbitrary shell commands. That's fast to build and dangerous to run. NEXUS
takes the slower, safer path:

```
Natural language
      ↓
Claude (tool-use / function-calling — proposes a plan, never executes)
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
   ollama pull llama3.1       # 8B, needs ~8GB RAM, best quality
   ollama pull qwen2.5:7b     # similar size, also good at tool calling
   ollama pull qwen2.5:3b     # ~4GB RAM, lighter but weaker at tool calling
   ```
3. **Install Python deps and run**:
   ```bash
   pip install -r requirements.txt
   python -m nexus.main
   ```

Ollama runs a local server automatically after install (`localhost:11434`) —
no need to start anything separately. If you want to use a different model
than the default, set `NEXUS_MODEL`:
```bash
export NEXUS_MODEL=qwen2.5:7b
python -m nexus.main
```

## Example session

```
you> find all my PDFs from last semester and put them in a folder called College
nexus> I found 14 PDFs in ~/Downloads and ~/Documents. I'll create a folder
       called College and move them there.

NEXUS wants to: create_folder(path='~/College')
Proceed? [y/N]: y
[auto] search_files(directory='~/Downloads', pattern='*.pdf')

NEXUS wants to: move_file(source='~/Downloads/notes1.pdf', destination_folder='~/College')
Proceed? [y/N]: y
...
nexus> Done — created ~/College and moved 14 PDFs into it.
```

## Project structure

```
nexus/
  tools.py          — the whitelisted actions NEXUS can take (the entire "surface area")
  permissions.py    — risk tiers + confirmation logic (the core safety layer)
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
- **Phase 2:** app control — open/close apps, basic window management, via
  OS-level APIs (no shell).
- **Phase 3 (highest risk, do last or skip):** a tightly whitelisted subset
  of read-only terminal commands (`ls`, `grep`, `git status`, etc.) with
  validated arguments — never free-form shell execution.

## Extending the tool registry

To add a new capability, add an entry to `TOOL_REGISTRY` in `tools.py` with:
1. A small, well-tested Python function
2. A `RiskTier`
3. A JSON-schema `input_schema` describing its arguments to Claude

Never add a tool that accepts a raw command string as an argument — that
defeats the entire point of the whitelist.
