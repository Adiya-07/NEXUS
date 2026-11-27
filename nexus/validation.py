"""Validate LLM tool arguments before permission checks or execution."""

import json


class ArgumentValidationError(ValueError):
    """A proposed tool call does not match its registered argument contract."""


def validate_arguments(name: str, args: object, schema: dict) -> dict:
    """Reject malformed arguments; never guess, coerce or rewrite user paths."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            raise ArgumentValidationError("Arguments contain invalid JSON.") from None

    if not isinstance(args, dict):
        raise ArgumentValidationError("Arguments must be a JSON object.")

    properties = schema["properties"]
    missing = [key for key in schema.get("required", []) if key not in args]
    if missing:
        raise ArgumentValidationError("Missing required arguments: " + ", ".join(missing))

    unknown = [str(key) for key in args if key not in properties]
    if unknown:
        raise ArgumentValidationError("Unexpected arguments: " + ", ".join(unknown))

    for key, value in args.items():
        expected = properties[key]["type"]
        if expected == "string":
            if type(value) is not str:
                raise ArgumentValidationError(f"'{key}' must be a string.")
            if not value.strip() or "\x00" in value:
                raise ArgumentValidationError(f"'{key}' must be non-blank and contain no NUL characters.")
        elif expected == "boolean":
            if type(value) is not bool:
                raise ArgumentValidationError(f"'{key}' must be a boolean (true or false).")
        else:
            raise ArgumentValidationError(f"Unsupported schema type for '{key}': {expected}.")

    if name == "rename_file":
        new_name = args["new_name"]
        if (
            new_name in (".", "..")
            or new_name.endswith((" ", "."))
            or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in new_name)
        ):
            raise ArgumentValidationError(
                "'new_name' must be a filename only, e.g. 'meeting_notes.txt', not a path."
            )

    return dict(args)
