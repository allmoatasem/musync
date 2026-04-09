"""PyWebView launcher for MuSync desktop app.

Usage:
    python app/main.py           # production — opens window loading dist/index.html
    python app/main.py --dev     # development — loads http://localhost:5173
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import urllib.request
from pathlib import Path

# Ensure the src tree is importable when run directly
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import webview  # pywebview

_log = logging.getLogger("musync-app")

PORT = 7765


def _start_server() -> None:
    """Start the FastAPI server in a background daemon thread."""
    from musync.server import serve
    _log.info(f"starting FastAPI server on port {PORT}")
    serve(PORT)


def _wait_for_server(timeout: float = 10.0) -> bool:
    """Poll until the server is up, returns True if ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=0.3)
            _log.info("server is ready")
            return True
        except Exception:
            time.sleep(0.1)
    _log.warning("server did not start in time")
    return False


def main() -> None:
    dev = "--dev" in sys.argv

    # Start FastAPI server (daemon — exits with the main process)
    t = threading.Thread(target=_start_server, daemon=True)
    t.start()
    _wait_for_server()

    if dev:
        url = "http://localhost:5173"
    else:
        # Bare path — pywebview serves via its own HTTP server
        # (file:// URLs disable pywebview's internal server)
        if getattr(sys, "frozen", False):
            base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
            url = str(base / "web" / "index.html")
        else:
            url = str(Path(__file__).parent / "dist" / "index.html")

    window = webview.create_window(
        title="MuSync",
        url=url,
        width=960,
        height=680,
        min_size=(720, 500),
    )

    webview.start(debug=dev)


if __name__ == "__main__":
    main()
