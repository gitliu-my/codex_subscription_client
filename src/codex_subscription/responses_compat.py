from __future__ import annotations

"""Translate public Responses requests into the Codex backend dialect."""

from dataclasses import dataclass
from typing import Any


LOCAL_REQUEST_FIELDS = frozenset(
    {"model", "input", "tools", "instructions", "stream"}
)

BACKEND_OPTION_FIELDS = frozenset(
    {
        "reasoning",
        "tool_choice",
        "parallel_tool_calls",
        "text",
        "include",
        "service_tier",
        "prompt_cache_key",
    }
)

# These are valid OpenAI-style client hints that the Codex subscription
# backend cannot currently enforce. Ignoring them keeps common SDK callers
# interoperable without pretending the requested behavior took effect.
IGNORED_COMPATIBILITY_FIELDS = frozenset(
    {
        "max_output_tokens",
        "prompt_cache_retention",
        "prompt_cache_options",
        "stream_options",
    }
)

KNOWN_UNSUPPORTED_FIELDS = frozenset(
    {
        "background",
        "conversation",
        "max_tool_calls",
        "metadata",
        "previous_response_id",
        "prompt",
        "safety_identifier",
        "temperature",
        "top_p",
        "truncation",
        "user",
    }
)

_REASONING_FIELDS = frozenset({"effort", "summary", "context", "mode"})
_TEXT_FIELDS = frozenset({"format", "verbosity"})


class ResponsesCompatibilityError(ValueError):
    """A public Responses parameter cannot be represented safely."""


@dataclass(frozen=True)
class ResponsesCompatibility:
    backend_options: dict[str, Any]
    ignored_fields: tuple[str, ...]


def translate_responses_options(body: dict[str, Any]) -> ResponsesCompatibility:
    """Validate request controls and return only backend-safe options."""

    known_fields = (
        LOCAL_REQUEST_FIELDS
        | BACKEND_OPTION_FIELDS
        | IGNORED_COMPATIBILITY_FIELDS
        | KNOWN_UNSUPPORTED_FIELDS
        | {"store"}
    )
    unknown = sorted(set(body) - known_fields)
    if unknown:
        raise ResponsesCompatibilityError(
            f"Unknown Responses parameter(s): {', '.join(unknown)}"
        )

    unsupported = sorted(
        name
        for name in set(body).intersection(KNOWN_UNSUPPORTED_FIELDS)
        if body[name] is not None
    )
    if unsupported:
        raise ResponsesCompatibilityError(
            "Unsupported Responses parameter(s) for the Codex subscription "
            f"backend: {', '.join(unsupported)}"
        )

    if body.get("store") not in {None, False}:
        raise ResponsesCompatibilityError(
            "Unsupported Responses parameter value: store must be false because "
            "csub does not provide persisted response state"
        )

    _validate_ignored_fields(body)
    backend_options: dict[str, Any] = {}

    if "reasoning" in body and body["reasoning"] is not None:
        reasoning = _object_field(body, "reasoning")
        unknown_reasoning = sorted(set(reasoning) - _REASONING_FIELDS)
        if unknown_reasoning:
            raise ResponsesCompatibilityError(
                "Unsupported reasoning parameter(s): "
                + ", ".join(unknown_reasoning)
            )
        for name in ("effort", "summary", "context", "mode"):
            if name in reasoning and reasoning[name] is not None:
                _string_value(reasoning[name], f"reasoning.{name}")
        backend_options["reasoning"] = dict(reasoning)

    if "tool_choice" in body and body["tool_choice"] is not None:
        tool_choice = body["tool_choice"]
        if not isinstance(tool_choice, (str, dict)):
            raise ResponsesCompatibilityError(
                "tool_choice must be a string or an object"
            )
        backend_options["tool_choice"] = tool_choice

    if "parallel_tool_calls" in body and body["parallel_tool_calls"] is not None:
        if not isinstance(body["parallel_tool_calls"], bool):
            raise ResponsesCompatibilityError("parallel_tool_calls must be a boolean")
        backend_options["parallel_tool_calls"] = body["parallel_tool_calls"]

    if "text" in body and body["text"] is not None:
        text = _object_field(body, "text")
        unknown_text = sorted(set(text) - _TEXT_FIELDS)
        if unknown_text:
            raise ResponsesCompatibilityError(
                "Unsupported text parameter(s): " + ", ".join(unknown_text)
            )
        if "verbosity" in text and text["verbosity"] is not None:
            _string_value(text["verbosity"], "text.verbosity")
        if "format" in text and text["format"] is not None:
            _object_value(text["format"], "text.format")
        backend_options["text"] = dict(text)

    if "include" in body and body["include"] is not None:
        include = body["include"]
        if not isinstance(include, list) or not all(
            isinstance(item, str) and item for item in include
        ):
            raise ResponsesCompatibilityError(
                "include must be an array of non-empty strings"
            )
        backend_options["include"] = list(include)

    for name in ("service_tier", "prompt_cache_key"):
        if name in body and body[name] is not None:
            backend_options[name] = _string_value(body[name], name)

    ignored = tuple(
        sorted(set(body).intersection(IGNORED_COMPATIBILITY_FIELDS))
    )
    return ResponsesCompatibility(backend_options, ignored)


def apply_backend_options(
    payload: dict[str, Any], backend_options: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge translated controls without allowing invariant replacement."""

    if not backend_options:
        return payload
    unknown = sorted(set(backend_options) - BACKEND_OPTION_FIELDS)
    if unknown:
        raise ResponsesCompatibilityError(
            "Unsafe Codex backend option(s): " + ", ".join(unknown)
        )

    for name in ("reasoning", "text"):
        if name in backend_options:
            value = _object_value(backend_options[name], name)
            payload[name] = {**payload[name], **value}

    if "include" in backend_options:
        include = backend_options["include"]
        if not isinstance(include, list) or not all(
            isinstance(item, str) and item for item in include
        ):
            raise ResponsesCompatibilityError(
                "include backend option must be an array of non-empty strings"
            )
        payload["include"] = list(dict.fromkeys([*include, *payload["include"]]))

    for name in (
        "tool_choice",
        "parallel_tool_calls",
        "service_tier",
        "prompt_cache_key",
    ):
        if name in backend_options:
            payload[name] = backend_options[name]
    return payload


def ignored_fields_header(fields: tuple[str, ...]) -> str | None:
    return ", ".join(fields) if fields else None


def _validate_ignored_fields(body: dict[str, Any]) -> None:
    if "max_output_tokens" in body and body["max_output_tokens"] is not None:
        value = body["max_output_tokens"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ResponsesCompatibilityError(
                "max_output_tokens must be a positive integer or null"
            )
    for name in ("prompt_cache_retention",):
        if name in body and body[name] is not None:
            _string_value(body[name], name)
    for name in ("prompt_cache_options", "stream_options"):
        if name in body and body[name] is not None:
            _object_value(body[name], name)


def _object_field(body: dict[str, Any], name: str) -> dict[str, Any]:
    return _object_value(body[name], name)


def _object_value(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResponsesCompatibilityError(f"{name} must be an object")
    return value


def _string_value(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResponsesCompatibilityError(f"{name} must be a non-empty string")
    return value
