from __future__ import annotations

import unittest

from codex_subscription.responses_compat import (
    ResponsesCompatibilityError,
    apply_backend_options,
    translate_responses_options,
)


class ResponsesCompatibilityTests(unittest.TestCase):
    def test_translates_supported_controls_and_classifies_ignored_hints(self) -> None:
        result = translate_responses_options(
            {
                "model": "gpt-test",
                "input": "hello",
                "stream": True,
                "reasoning": {"effort": "high", "summary": "detailed"},
                "tool_choice": "required",
                "parallel_tool_calls": False,
                "text": {"verbosity": "low"},
                "include": ["file_search_call.results"],
                "store": False,
                "service_tier": "auto",
                "prompt_cache_key": "session-123",
                "max_output_tokens": 32_000,
                "prompt_cache_retention": "24h",
                "prompt_cache_options": {"mode": "explicit"},
            }
        )

        self.assertEqual(result.backend_options["tool_choice"], "required")
        self.assertFalse(result.backend_options["parallel_tool_calls"])
        self.assertEqual(result.backend_options["reasoning"]["summary"], "detailed")
        self.assertEqual(result.backend_options["prompt_cache_key"], "session-123")
        self.assertEqual(result.backend_options["service_tier"], "auto")
        self.assertEqual(
            result.ignored_fields,
            (
                "max_output_tokens",
                "prompt_cache_options",
                "prompt_cache_retention",
            ),
        )

    def test_backend_merge_preserves_protocol_invariants(self) -> None:
        payload = {
            "model": "gpt-test",
            "input": [],
            "instructions": "base",
            "tools": [],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "store": False,
            "stream": True,
            "reasoning": {"effort": "medium", "summary": "auto"},
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
        }

        merged = apply_backend_options(
            payload,
            {
                "reasoning": {"summary": "detailed"},
                "text": {"verbosity": "low"},
                "include": ["file_search_call.results"],
                "tool_choice": "required",
            },
        )

        self.assertEqual(merged["model"], "gpt-test")
        self.assertFalse(merged["store"])
        self.assertTrue(merged["stream"])
        self.assertEqual(merged["reasoning"]["effort"], "medium")
        self.assertEqual(merged["reasoning"]["summary"], "detailed")
        self.assertEqual(
            merged["include"],
            ["file_search_call.results", "reasoning.encrypted_content"],
        )

    def test_rejects_unknown_and_semantically_unsupported_fields(self) -> None:
        with self.assertRaisesRegex(
            ResponsesCompatibilityError, "Unknown Responses parameter.*mystery"
        ):
            translate_responses_options({"input": "hello", "mystery": True})
        with self.assertRaisesRegex(
            ResponsesCompatibilityError, "Unsupported Responses parameter.*temperature"
        ):
            translate_responses_options({"input": "hello", "temperature": 0.2})
        with self.assertRaisesRegex(ResponsesCompatibilityError, "store must be false"):
            translate_responses_options({"input": "hello", "store": True})

        unset = translate_responses_options(
            {"input": "hello", "temperature": None, "previous_response_id": None}
        )
        self.assertEqual(unset.backend_options, {})

    def test_rejects_invalid_ignored_field_values(self) -> None:
        with self.assertRaisesRegex(
            ResponsesCompatibilityError, "max_output_tokens must be a positive integer"
        ):
            translate_responses_options(
                {"input": "hello", "max_output_tokens": 0}
            )


if __name__ == "__main__":
    unittest.main()
