"""Tests for `scripts/smoke.py`.

The e-mail assertion in the smoke script only runs against the compose stack, in CI, where a
Celery worker and Mailpit exist. Without these tests the code that polls the inbox would be
exercised for the first time exactly when it is needed — which is how the recipient matching
shipped with a bug: it keyed the parsed `To:` headers by the recipient's display *name* and
then looked the entry up under `"to"`, so it matched nothing at all.

`scripts/` is not on `sys.path` and not covered by `mypy`, so it is loaded by path here. The
result is typed as `Any` deliberately: the script is a linear HTTP driver, and re-declaring
its types in a test would only let the two drift apart.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

pytestmark = pytest.mark.unit

SMOKE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "smoke.py"


def _load_smoke() -> Any:
    spec = importlib.util.spec_from_file_location("taskhub_smoke_under_test", SMOKE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke()


class _Mailpit(BaseHTTPRequestHandler):
    """The slice of Mailpit's API that `wait_for_mail` depends on."""

    #: Class-level on purpose: the handler runs on the server's own thread, so the fixtures
    #: set these to publish state into it.
    messages: ClassVar[list[dict[str, Any]]] = []
    delay: ClassVar[float] = 0.0

    def do_GET(self) -> None:
        if not self.path.startswith("/api/v1/messages"):
            self.send_error(404)
            return
        if _Mailpit.delay:
            time.sleep(_Mailpit.delay)
        body = json.dumps({"messages": _Mailpit.messages}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Keep pytest's output clean."""


@pytest.fixture
def mailpit() -> Any:
    server = HTTPServer(("127.0.0.1", 0), _Mailpit)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Mailpit.messages = []
    _Mailpit.delay = 0.0
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    # `shutdown` stops the loop but leaves the listening socket open, and `filterwarnings`
    # turns the resulting ResourceWarning into an error.
    server.server_close()


def _message(recipient: str, *, subject: str = "s", identifier: str = "1") -> dict[str, Any]:
    return {
        "ID": identifier,
        "To": [{"Name": "Somebody", "Value": recipient}],
        "Subject": subject,
    }


class TestAddressedTo:
    @pytest.mark.parametrize(
        "value",
        [
            "someone@taskhub.dev",
            "Someone@TaskHub.dev",
            "  someone@taskhub.dev  ",
            "Somebody <someone@taskhub.dev>",
        ],
    )
    def test_the_address_matches_regardless_of_case_and_form(self, value: str) -> None:
        assert smoke.addressed_to("someone@taskhub.dev", _message(value)) is True

    def test_a_message_for_somebody_else_does_not_match(self) -> None:
        assert smoke.addressed_to("someone@taskhub.dev", _message("other@taskhub.dev")) is False

    def test_a_message_with_no_recipients_does_not_match(self) -> None:
        assert smoke.addressed_to("someone@taskhub.dev", {"ID": "1"}) is False

    def test_a_second_recipient_on_the_same_message_counts(self) -> None:
        message = {
            "ID": "1",
            "To": [
                {"Name": "cc", "Value": "watcher@taskhub.dev"},
                {"Name": "to", "Value": "someone@taskhub.dev"},
            ],
        }
        assert smoke.addressed_to("someone@taskhub.dev", message) is True

    def test_the_display_name_alone_is_not_an_address(self) -> None:
        """The bug this file exists for: matching on `Name` matched nothing."""
        message = {"ID": "1", "To": [{"Name": "someone@taskhub.dev", "Value": "real@x.dev"}]}
        assert smoke.addressed_to("someone@taskhub.dev", message) is False


class TestWaitForMail:
    def test_an_empty_inbox_gives_up_and_returns_none(self, mailpit: str) -> None:
        started = time.monotonic()
        assert smoke.wait_for_mail(mailpit, "someone@taskhub.dev", timeout=2.0) is None
        # It must have actually polled for the whole window rather than giving up at once.
        assert time.monotonic() - started >= 1.9

    def test_the_matching_message_is_returned(self, mailpit: str) -> None:
        _Mailpit.messages = [
            _message("other@taskhub.dev", identifier="1"),
            _message("someone@taskhub.dev", subject="Please look at this", identifier="2"),
        ]
        found = smoke.wait_for_mail(mailpit, "someone@taskhub.dev", timeout=5.0)
        assert found is not None
        assert found["ID"] == "2"
        assert "Please look at this" in json.dumps(found)

    def test_a_message_that_arrives_late_is_still_found(self, mailpit: str) -> None:
        # The normal race: the HTTP response has returned but the worker is still talking to
        # SMTP, so the assertion has to wait rather than fail.
        def arrive() -> None:
            time.sleep(1.5)
            _Mailpit.messages = [_message("late@taskhub.dev", identifier="3")]

        threading.Thread(target=arrive, daemon=True).start()
        assert smoke.wait_for_mail(mailpit, "late@taskhub.dev", timeout=10.0) is not None

    def test_an_unreachable_sink_returns_none_instead_of_raising(self) -> None:
        # Port 1 is reserved and nothing listens on it.
        assert smoke.wait_for_mail("http://127.0.0.1:1", "x@taskhub.dev", timeout=2.0) is None

    def test_a_slow_sink_is_tolerated(self, mailpit: str) -> None:
        _Mailpit.messages = [_message("someone@taskhub.dev")]
        _Mailpit.delay = 0.4
        assert smoke.wait_for_mail(mailpit, "someone@taskhub.dev", timeout=10.0) is not None
