"""A local Redis stand-in, so the Redis-dependent tests can run without Docker.

Why this exists
---------------
Six tests in this repository are skipped when Redis is unreachable: the cache
round-trip, the sliding rate-limit window, and the readiness behaviour with Redis down.
On CI that is fine (there is a `redis:7` service container). On a developer laptop it
meant those tests quietly stopped running, and the degradation path — the one that
decides whether a dead Redis breaks the API or not — was never exercised locally.

What it actually is
-------------------
`fakeredis.TcpFakeServer` speaks real RESP over TCP, so the project's own `redis-py`
client talks to it with no patching at all: not a mock, not a stubbed client, but a
server listening on a real port. That is also why it lives in its own virtualenv —
`fakeredis` must never enter the project dependencies.

Usage, from the repository root::

    python -m venv .venv-redis
    .venv-redis\\Scripts\\pip install fakeredis      # Windows
    .venv-redis/Scripts/pip install fakeredis        # macOS / Linux
    .venv-redis\\Scripts\\python tools\\redis_stub.py

Confirm the tests stopped skipping::

    pytest tests/unit/test_cache.py -v
    # test_corrupt_json_is_dropped_not_raised should report PASSED, not SKIPPED

Stop it again (PowerShell)::

    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*redis_stub*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId }
"""

from __future__ import annotations

import argparse
import logging
import os
import threading

from fakeredis import TcpFakeServer

LOGGER = logging.getLogger("redis_stub")


def main() -> None:
    """Listen on a TCP port and answer RESP until interrupted."""
    parser = argparse.ArgumentParser(description="Fake Redis over TCP, for local tests")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    server = TcpFakeServer((args.host, args.port), server_type="redis")
    thread = threading.Thread(target=server.serve_forever, name="redis-stub", daemon=True)
    thread.start()
    LOGGER.info("redis stub listening on %s:%s (pid %s)", args.host, args.port, os.getpid())

    try:
        while thread.is_alive():
            thread.join(timeout=1.0)
    except KeyboardInterrupt:
        LOGGER.info("stopping")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
