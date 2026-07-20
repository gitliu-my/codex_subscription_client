from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from codex_subscription.server import SubscriptionApiServer
from codex_subscription.service import (
    ApiServiceStatus,
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
            stopped, status = stop_api_service(config)
            self.assertTrue(stopped)
            self.assertEqual(status.state, "stopped")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    @patch("codex_subscription.service.subprocess.Popen")
    @patch("codex_subscription.service._serve_command", return_value=["csub", "serve"])
    @patch("codex_subscription.service.probe_api")
    def test_start_detaches_and_waits_until_running(
        self,
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
