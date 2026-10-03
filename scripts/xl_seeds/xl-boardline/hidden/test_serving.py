"""Serving contract: routes, status codes, content types, the CLI line (README §1, §3)."""

from __future__ import annotations

import http.client
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def test_get_board_200_html(client: Any) -> None:
    result = client("GET", "/")
    assert result.status == 200
    assert result.content_type.startswith("text/html")
    assert "utf-8" in result.content_type
    assert "Task board" in result.body


def test_healthz(client: Any) -> None:
    result = client("GET", "/healthz")
    assert result.status == 200
    assert result.content_type.startswith("text/plain")
    assert result.body.strip() == "ok"


def test_unknown_path_404(client: Any) -> None:
    result = client("GET", "/nope")
    assert result.status == 404


def test_wrong_method_405(client: Any) -> None:
    assert client("GET", "/tasks").status == 405
    assert client("POST", "/healthz").status == 405
    assert client("POST", "/").status == 405


def test_cli_listening_line_and_serve(data_path: Path) -> None:
    """``python -m boardline`` prints the listening line (real port) before serving."""
    from conftest import WS

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "boardline",
            "--port",
            "0",
            "--data",
            str(data_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(WS), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        line = proc.stdout.readline().strip()  # type: ignore[union-attr]
        match = re.fullmatch(r"listening on http://127\.0\.0\.1:(\d+)/", line)
        assert match, f"unexpected first line: {line!r}"
        port = int(match.group(1))
        deadline = time.time() + 10
        while True:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/healthz")
                response = conn.getresponse()
                body = response.read().decode("utf-8")
                conn.close()
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.1)
        assert response.status == 200
        assert body.strip() == "ok"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
