"""
main.py — NEXUS CLI entry point, running on a free local Ollama model.

Run with: python -m nexus.main
Requires Ollama installed and running locally, with a tool-capable model
pulled (see nexus/ollama_planner.py for setup instructions).

This is Phase 1 of NEXUS: natural-language file operations only, with a
full permission/confirmation layer. No raw shell execution, no app control
yet — see README.md for the roadmap.
"""

import sys
from .ollama_planner import OllamaPlanner
from .executor import execute_tool_call, print_history
from .permission_commands import handle_permission
from .path_policy import AccessPolicy, DEFAULT_WORKSPACE, PathDenied


BANNER = """
 _   _ _______  ___   _ ____
| \\ | | ____\\ \\/ / | | / ___|
|  \\| |  _|  \\  /| | | \\___ \\
| |\\  | |___ /  \\| |_| |___) |
|_| \\_|_____/_/\\_\\\\___/|____/

NEXUS — Phase 1 (file operations only) — running locally via Ollama, free & unlimited
Type a request in plain English, or 'history' to see past actions, or 'quit' to exit.
"""


def main():
    print(BANNER)
    try:
        policy = AccessPolicy.load()
        if policy.visible(DEFAULT_WORKSPACE) and not DEFAULT_WORKSPACE.exists():
            policy.check(DEFAULT_WORKSPACE, write=True).mkdir(exist_ok=True)
    except (PathDenied, OSError) as exc:
        print(f"Workspace unavailable: {exc}")
    print("Type 'permissions' to view access rules. Default workspace: ~/nexus_test")
    try:
        planner = OllamaPlanner()
    except Exception as e:
        print(f"Couldn't connect to Ollama: {e}")
        print("Make sure Ollama is installed and running: https://ollama.com/download")
        sys.exit(1)

    messages = []

    while True:
        try:
            user_input = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            sys.exit(0)

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            print("bye.")
            break
        if user_input.lower() == "history":
            print_history()
            continue

        if handle_permission(user_input):
            messages.clear()
            continue

        messages.append({"role": "user", "content": user_input})

        # Loop until the model stops requesting tool calls and gives a final answer.
        max_steps = 10  # safety valve against local-model loops
        for _ in range(max_steps):
            try:
                assistant_msg = planner.run_turn(messages)
            except Exception as e:
                print(f"\nnexus> (error talking to Ollama: {e})")
                break

            messages.append(assistant_msg)

            content = assistant_msg.get("content", "")
            if content and content.strip():
                print(f"\nnexus> {content.strip()}")

            tool_calls = assistant_msg.get("tool_calls") or []
            if not tool_calls:
                break  # model gave a final answer, no more tools to run.

            for call in tool_calls:
                fn = call["function"]
                name = fn.get("name")
                args = fn.get("arguments")

                outcome = execute_tool_call(name, args)
                messages.append({
                    "role": "tool",
                    "content": str(outcome.get("result", outcome.get("error", ""))),
                })
        else:
            print("\nnexus> (stopped after too many steps — try rephrasing your request)")


if __name__ == "__main__":
    main()
