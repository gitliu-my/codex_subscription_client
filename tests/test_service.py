from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from codex_subscription.server import SubscriptionApiServer
from codex_subscription.service import (
    ApiServiceStatus,
    _MacOSLaunchAgent,
    probe_api,
    start_api_service,
    stop_api_service,
)


class ApiServiceTests(unittest.TestCase):
    def test_probe_and_stop_use_authenticated_control_endpoints(self) -> None:
        api_key = "local-test-api-key-1234567890"
        server = SubscriptionApiServer(
            ("127.0.0.1", 0), MagicMock(), api_key=api_key
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = {
            "host": "127.0.0.1",
            "port": server.server_port,
            "api_key": api_key,
        }
        try:
            status = probe_api(config)
            self.assertEqual(status.state, "running")
            self.assertIsNotNone(status.pid)
            self.assertEqual(
                probe_api({**config, "api_key": "wrong-api-key"}).state,
                "key_mismatch",
            )
            with patch(
                "codex_subscription.service._detect_macos_launch_agent",
                return_value=None,
            ):
                stopped, status = stop_api_service(config, settle_time=0)
            self.assertTrue(stopped)
            self.assertEqual(status.state, "stopped")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    @patch("codex_subscription.service.subprocess.Popen")
    @patch("codex_subscription.service._serve_command", return_value=["csub", "serve"])
    @patch("codex_subscription.service.probe_api")
    @patch(
        "codex_subscription.service._detect_macos_launch_agent",
        return_value=None,
    )
    def test_start_detaches_and_waits_until_running(
        self,
        detect_launch_agent: MagicMock,
        probe: MagicMock,
        serve_command: MagicMock,
        popen: MagicMock,
    ) -> None:
        probe.side_effect = [ApiServiceStatus("stopped"), ApiServiceStatus("running", 42)]
        popen.return_value.poll.return_value = None
        config = {"host": "127.0.0.1", "port": 8317, "api_key": "x" * 32}
        with tempfile.TemporaryDirectory() as directory:
            started, status = start_api_service(
                config, log_path=Path(directory) / "api.log"
            )

        self.assertTrue(started)
        self.assertEqual(status.pid, 42)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.kwargs["stdin"], -3)

    @patch("codex_subscription.service._bootstrap_macos_launch_agent")
    @patch("codex_subscription.service._detect_macos_launch_agent")
    @patch("codex_subscription.service.probe_api")
    def test_start_bootstraps_matching_macos_launch_agent(
        self,
        probe: MagicMock,
        detect_launch_agent: MagicMock,
        bootstrap: MagicMock,
    ) -> None:
        agent = _MacOSLaunchAgent(
            path=Path("/tmp/com.gitliu-my.csub-api.plist"),
            label="com.gitliu-my.csub-api",
            loaded=False,
        )
        detect_launch_agent.return_value = agent
        probe.side_effect = [
            ApiServiceStatus("stopped"),
            ApiServiceStatus("running", 42),
        ]
        config = {"host": "127.0.0.1", "port": 8317, "api_key": "x" * 32}

        started, status = start_api_service(config)

        self.assertTrue(started)
        self.assertEqual(status.pid, 42)
        bootstrap.assert_called_once_with(agent)

    @patch("codex_subscription.service._bootout_macos_launch_agent")
    @patch("codex_subscription.service._detect_macos_launch_agent")
    @patch("codex_subscription.service.probe_api")
    def test_stop_boots_out_keepalive_launch_agent(
        self,
        probe: MagicMock,
        detect_launch_agent: MagicMock,
        bootout: MagicMock,
    ) -> None:
        agent = _MacOSLaunchAgent(
            path=Path("/tmp/com.gitliu-my.csub-api.plist"),
            label="com.gitliu-my.csub-api",
            loaded=True,
            pid=42,
        )
        detect_launch_agent.return_value = agent
        probe.side_effect = [
            ApiServiceStatus("running", 42),
            ApiServiceStatus("stopped"),
        ]
        config = {"host": "127.0.0.1", "port": 8317, "api_key": "x" * 32}

        stopped, status = stop_api_service(config, settle_time=0)

        self.assertTrue(stopped)
        self.assertEqual(status.state, "stopped")
        bootout.assert_called_once_with(agent)
