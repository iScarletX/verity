"""Entry point: ``python -m verity.web``.

Refuses to bind anything other than a loopback host. There is no
``--allow-external`` flag; adding one would deserve its own security
review round.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import sys


def _is_loopback_host(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="verity.web",
                                 description="Local Verity Web MVP")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)

    if not _is_loopback_host(args.host):
        print(f"refusing to bind non-loopback host: {args.host!r}", file=sys.stderr)
        print("Verity's Web MVP only accepts loopback (127.0.0.1, ::1, localhost).",
              file=sys.stderr)
        return 2

    # Import here so ``python -m verity.web --help`` works without uvicorn.
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is required to run the Verity Web MVP. "
              "Install with: pip install -r requirements.lock", file=sys.stderr)
        return 3
    from . import create_app

    app = create_app()
    print(f"Verity local Web MVP on http://{args.host}:{args.port}/  "
          f"(Ctrl+C to stop)", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
