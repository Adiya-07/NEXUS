"""
main.py — VEYL CLI entry point, running on a free local Ollama model.

Run with: python -m nexus.main
Requires Ollama installed and running locally, with a tool-capable model
pulled (see nexus/ollama_planner.py for setup instructions).

This is Phase 1 of VEYL: natural-language file operations only, with a
full permission/confirmation layer. No raw shell execution, no app control
yet — see README.md for the roadmap.
"""

import sys
from time import perf_counter, monotonic
from .fast_path import run_fast_command
from .ollama_planner import OllamaPlanner
from .executor import execute_tool_call, print_history
from .permission_commands import handle_permission
from .recovery import handle_recovery, cleanup_expired
from .path_policy import AccessPolicy, DEFAULT_WORKSPACE, PathDenied


BANNER = r"""
 __     __ _____ __   __ _
 \ \   / /| ____|\ \ / /| |
  \ \ / / |  _|   \ V / | |
   \ V /  | |___   | |  | |___
    \_/   |_____|  |_|  |_____|

VEYL — Phase 1.5 (file operations only) — running locally via Ollama
Type a request in plain English, 'history' for actions, or 'quit' to exit.
"""


def main():
    print(BANNER)
    cleanup_expired(show=print)
    last_cleanup = monotonic()
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

    print(f"AI model: {planner.model} | Fast path enabled")
    print("Type recycle bin to list deleted files; restore <ID> to recover one.")
    messages = []

    while True:
        try:
            user_input = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            sys.exit(0)

        if monotonic() - last_cleanup >= 3600:
            cleanup_expired(show=print)
            last_cleanup = monotonic()

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

        if handle_recovery(user_input):
            messages.clear()
            continue

        started = perf_counter()
        try:
            fast_result = run_fast_command(user_input)
        except (KeyboardInterrupt, EOFError):
            print("\nAction cancelled.")
            continue
        if fast_result is not None:
            print(f"\nveyl> {fast_result}")
            print(f"[Python | {perf_counter() - started:.2f}s elapsed, including confirmation]")
            # Keep the direct result for later conversational references.
            messages.extend([{"role": "user", "content": user_input},
                             {"role": "assistant", "content": fast_result}])
            continue

        messages.append({"role": "user", "content": user_input})
        ai_seconds = 0.0
        ai_calls = 0

        # Loop until the model stops requesting tool calls and gives a final answer.
        max_steps = 10  # safety valve against local-model loops
        for _ in range(max_steps):
            try:
                ai_started = perf_counter()
                ai_calls += 1
                assistant_msg = planner.run_turn(messages)
                ai_seconds += perf_counter() - ai_started
            except Exception as e:
                ai_seconds += perf_counter() - ai_started
                print(f"\nveyl> (error talking to Ollama: {e})")
                break

            messages.append(assistant_msg)

            content = assistant_msg.get("content", "")
            if content and content.strip():
                print(f"\nveyl> {content.strip()}")

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
                    "tool_name": name,
                    "content": str(outcome.get("result", outcome.get("error", ""))),
                })
        else:
            print("\nveyl> (stopped after too many steps — try rephrasing your request)")
        print(f"[AI | {ai_seconds:.2f}s inference | {ai_calls} calls | {perf_counter() - started:.2f}s total]")


if __name__ == "__main__":
    main()
