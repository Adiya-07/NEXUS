"""
ollama_planner.py — Turns a natural language request into a sequence of
proposed tool calls, using a local Ollama model instead of a paid API.

Free, unlimited, runs entirely on your laptop, no internet required after
the model is downloaded. Tradeoff: local models are noticeably less
reliable at tool-calling than Claude — expect more retries/clarifications
on complex multi-step requests. For straightforward file operations it
works fine.

Setup:
    1. Install Ollama: https://ollama.com/download
    2. Pull a tool-capable model, e.g.:
         ollama pull qwen3:4b       (default, lightweight)
         ollama pull qwen2.5:7b      (similar size, good at tool calling)
         ollama pull qwen2.5:3b      (lighter, ~4GB RAM, lower quality)
    3. Make sure the Ollama app/service is running (it runs a local server
       on http://localhost:11434 automatically after install).
"""

import os
import ollama

from .tools import ollama_tool_schemas

MODEL = os.environ.get("NEXUS_MODEL", "qwen3:4b")

SYSTEM_PROMPT = """You are NEXUS, an AI that helps a user operate their computer \
through natural language. You do NOT have direct access to the filesystem — you can \
only propose calls to the tools you've been given. You never invent tools that \
aren't in your tool list, and you never ask the user to run shell commands manually.

When a request needs multiple steps (e.g. "find my PDFs and put them in a new \
folder"), call tools one at a time, using the result of one call to inform the \
next. Be conservative: if a request is ambiguous, ask a clarifying question in \
plain text instead of guessing which files to touch.

Use absolute paths or ~/paths, never relative paths. The default approved
workspace is ~/nexus_test. Only the user can change access rules via direct
terminal commands. Never claim a permission change succeeded yourself.

After tools have been run and you have their results, give the user a short, \
plain-language summary of what happened.
"""


class OllamaPlanner:
    def __init__(self, model: str | None = None):
        self.model = model or MODEL
        self.tools = ollama_tool_schemas()

    def run_turn(self, messages: list[dict]) -> dict:
        """Send the conversation so far to the local model and get back its
        next move (either tool_calls to execute, or a plain text reply)."""
        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
        response = ollama.chat(
            model=self.model,
            messages=full_messages,
            tools=self.tools,
            options={"num_ctx": 4096},
        )
        return response["message"].model_dump(exclude_none=True)
