#!/usr/bin/env python3
"""Safe launcher for the Verity local Web MVP.

Design points:

- Resolves the project root from the script's own location — the launcher
  works no matter where the user invokes it from.
- Sanity-checks Python version and that the ``verity.web`` package can
  be imported. Bandit / gitleaks availability is only reported, never
  auto-installed. (Auto-installing would silently mutate the user's
  environment.)
- Refuses non-loopback hosts. There is intentionally no override flag
  in this round.
- Runs uvicorn in the foreground; Ctrl+C stops it cleanly.
- Optional ``webbrowser.open`` on ready; ``--no-browser`` disables it.
- Does NOT daemonize, does NOT write PID files, does NOT modify PATH.
- Port-in-use is surfaced with a plain-language error; the launcher does
  NOT kill any other process.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
SRC = PROJECT_ROOT / "src"


def _is_loopback_host(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _preflight() -> list[str]:
    problems: list[str] = []
    if sys.version_info < (3, 9):
        problems.append(
            f"Python 3.9+ required; running {sys.version.split()[0]}"
        )
    # Make src/ importable if the user runs the launcher directly against
    # a source checkout (i.e. without an editable install).
    if str(SRC) not in sys.path and SRC.is_dir():
        sys.path.insert(0, str(SRC))
    try:
        import verity.web  # noqa: F401
    except Exception as e:
        problems.append(f"Cannot import verity.web ({e}); "
                        f"install with `pip install -e .` from {PROJECT_ROOT}")
    for name, hint in (
        ("starlette", "pip install -r requirements.lock"),
        ("uvicorn", "pip install -r requirements.lock"),
    ):
        try:
            __import__(name)
        except Exception:
            problems.append(f"Missing dependency: {name}. Install with `{hint}`.")
    return problems


def _port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
        return True
    except OSError:
        return False


def _open_browser_later(url: str, delay: float = 1.0) -> None:
    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url, new=1, autoraise=True)
        except Exception:
            pass
    t = threading.Thread(target=_open, daemon=True)
    t.start()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="start_local_web",
                                 description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true",
                    help="Do not open the browser automatically.")
    ap.add_argument("--check-only", action="store_true",
                    help="Run pre-flight checks and exit without binding.")
    args = ap.parse_args(argv)

    if not _is_loopback_host(args.host):
        print(f"refusing to bind non-loopback host: {args.host!r}", file=sys.stderr)
        print("Verity's Web MVP only accepts loopback (127.0.0.1, ::1, localhost).",
              file=sys.stderr)
        return 2

    problems = _preflight()
    if problems:
        print("Pre-flight checks found problems:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 4

    # Report gitleaks / bandit as informational; do NOT install anything.
    try:
        from verity.gitleaks_runner import GitleaksRunner
        ok, _reason, version, _sha = GitleaksRunner().check_binary()
        print(f"[verity] gitleaks: "
              f"{'available ' + (version or '') if ok else 'NOT available (Skill Secret coverage will be marked insufficient under --profile standard)'}",
              file=sys.stderr)
    except Exception:
        pass

    if args.check_only:
        print("pre-flight ok", file=sys.stderr)
        return 0

    if not _port_available(args.host, args.port):
        print(f"[verity] port {args.host}:{args.port} is already in use. "
              f"Choose a different --port, or free the port yourself; "
              f"the launcher will not kill other processes.",
              file=sys.stderr)
        return 5

    from verity.web import create_app
    import uvicorn

    app = create_app()
    url = f"http://{args.host}:{args.port}/"
    print(f"[verity] starting on {url}  (Ctrl+C to stop)", file=sys.stderr)
    if not args.no_browser:
        _open_browser_later(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
