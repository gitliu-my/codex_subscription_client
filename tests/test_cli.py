from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from codex_subscription.cli import (
    _choose_configuration,
    _resolve_client_defaults,
    main,
)
from codex_subscription.client import SubscriptionModel


def model(
    slug: str,
    default: str,
    efforts: tuple[str, ...],
) -> SubscriptionModel:
    return SubscriptionModel(
        slug=slug,
        display_name=slug.upper(),
        description="",
        default_reasoning_effort=default,
        supported_reasoning_efforts=efforts,
        input_modalities=("text", "image"),
    )


class CliConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.models = [
            model("gpt-luna", "low", ("low", "medium")),
            model("gpt-sol", "medium", ("low", "medium", "high")),
        ]

    def test_menu_uses_saved_defaults_as_initial_selection(self) -> None:
        calls: list[tuple[str, list[str], int]] = []
        choices = iter([0, 1])

        def selector(title: str, options: list[str], selected: int) -> int:
            calls.append((title, options, selected))
            return next(choices)

        selected_model, effort = _choose_configuration(
            self.models,
            {"model": "gpt-sol", "reasoning_effort": "high"},
            selector=selector,
        )

        self.assertEqual(calls[0][2], 1)
        self.assertEqual(calls[1][2], 0)
        self.assertEqual(selected_model.slug, "gpt-luna")
        self.assertEqual(effort, "medium")

    def test_no_command_prints_help_without_error(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = main([])

        self.assertEqual(result, 0)
        self.assertIn("usage: csub", output.getvalue())

    def test_explicit_values_work_without_interactive_menu(self) -> None:
        def unexpected_selector(title: str, options: list[str], selected: int) -> int:
            raise AssertionError("selector should not be opened")

        selected_model, effort = _choose_configuration(
            self.models,
            {"model": "gpt-luna", "reasoning_effort": "low"},
            requested_model="gpt-sol",
            requested_effort="high",
            selector=unexpected_selector,
        )
        self.assertEqual(selected_model.slug, "gpt-sol")
        self.assertEqual(effort, "high")

    def test_unsupported_effort_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "不支持推理档位"):
            _choose_configuration(
                self.models,
                {"model": "gpt-luna", "reasoning_effort": "low"},
                requested_model="gpt-luna",
                requested_effort="high",
            )

    def test_cli_and_environment_override_saved_defaults(self) -> None:
        config = {"model": "saved-model", "reasoning_effort": "low"}
        with patch.dict(
            os.environ,
            {
                "CODEX_SUBSCRIPTION_MODEL": "env-model",
                "CODEX_SUBSCRIPTION_REASONING_EFFORT": "medium",
            },
        ):
            self.assertEqual(
                _resolve_client_defaults(config, None, None),
                ("env-model", "medium"),
            )
            self.assertEqual(
                _resolve_client_defaults(config, "cli-model", "high"),
                ("cli-model", "high"),
            )

    @patch("codex_subscription.cli.serve")
    @patch("codex_subscription.cli.SettingsStore")
    def test_serve_uses_shared_saved_configuration(
        self,
        store_class: MagicMock,
        serve_mock: MagicMock,
    ) -> None:
        store_class.return_value.load_or_create.return_value = {
            "host": "127.0.0.1",
            "port": 9123,
            "api_key": "saved-local-api-key-1234567890",
            "model": "saved-model",
            "reasoning_effort": "medium",
        }

        self.assertEqual(main(["serve", "--no-login"]), 0)

        host, port, api_key, client = serve_mock.call_args.args
        self.assertEqual((host, port), ("127.0.0.1", 9123))
        self.assertEqual(api_key, "saved-local-api-key-1234567890")
        self.assertEqual(client.model, "saved-model")
        self.assertEqual(client.reasoning_effort, "medium")
        self.assertFalse(client.allow_login)

if __name__ == "__main__":
    unittest.main()
