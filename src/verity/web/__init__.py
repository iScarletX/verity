"""Verity local Web MVP.

Read-only front-end over the existing static pipeline. Does NOT execute
skills, install dependencies, contact LLMs, or do anything the CLI can't.

- Binds to 127.0.0.1 by default; refuses non-loopback hosts unless a
  future explicit dangerous flag is added (not in this round).
- Every request path terminates in the same ``verity.review.run_review``
  code path used by the CLI.
- All static assets ship inside the wheel; no CDN / external font / etc.
"""

from .app import create_app

__all__ = ["create_app"]
