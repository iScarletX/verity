"""Generates the ``sandbox-exec`` (Seatbelt) profile used to run a
reviewed Skill's entry point.

The profile is intentionally minimal and denies-by-default:

- ``(deny default)`` — nothing is allowed unless explicitly allowed below.
- ``(allow file-read*)`` — the interpreter, stdlib, and any files on disk
  can be *read*. This is required for ``python3`` itself to start up (it
  needs to read its own binary, shared libraries, and the standard
  library). Read access does not let the reviewed script exfiltrate data
  anywhere — outbound network is denied (no ``network-outbound`` allow
  clause below) and there is no write path outside the sandbox tmpdir.
- ``(allow file-write* (subpath "<SANDBOX_TMPDIR>"))`` — the reviewed
  script may only *write* inside its own private staging tmpdir. Any
  write attempt elsewhere is denied by Seatbelt itself (defense in
  depth beneath the Python-level audit hook, which only observes and
  cannot itself block).
- ``(allow process-exec)`` / ``(allow process-fork)`` — needed so the
  Python interpreter can start subprocesses/threads at all (the runner
  still audits every ``subprocess.Popen``/``os.exec*`` call via the
  driver's audit hook; Seatbelt process-exec allowance does not defeat
  that observation).
- ``(allow signal)`` — needed for the watchdog to deliver SIGKILL/SIGXCPU
  and for the interpreter's own signal handling.
- ``(allow sysctl-read)`` — CPython reads a handful of sysctls at
  startup (e.g. for ``os.cpu_count()``); without this the interpreter
  fails to even start under Seatbelt.
- No ``network-outbound`` / ``network*`` allow clause at all. This is
  the enforcement point for "no network for the reviewed skill": Verity
  does not carve out any host or port. Verified manually against a
  live ``urllib.request.urlopen`` probe (see the sandboxed integration
  test in ``tests/test_sandbox.py``).

The profile text has exactly one caller-supplied value: the absolute,
already-created sandbox tmpdir path, which is escaped for embedding in
a Seatbelt s-expression string literal.
"""

from __future__ import annotations


def _escape_sb_string(path: str) -> str:
    """Escape a path for embedding inside a Seatbelt ``"..."`` literal.

    Seatbelt profile strings use ordinary Scheme-style string escaping:
    backslash and double-quote must be escaped. Paths under a tmpdir we
    created ourselves never contain these characters in practice, but we
    escape defensively since this string is built from a runtime path.
    """
    return path.replace("\\", "\\\\").replace('"', '\\"')


def build_sandbox_profile(tmpdir: str) -> str:
    """Return the full ``.sb`` profile text scoped to ``tmpdir``.

    ``tmpdir`` must be an absolute, already-resolved path (the caller is
    responsible for calling this only after the staging directory has
    been created and, ideally, realpath-resolved so writes cannot escape
    through an unresolved symlink component).
    """
    escaped = _escape_sb_string(tmpdir)
    return (
        "(version 1)\n"
        "(deny default)\n"
        "(allow file-read*)\n"
        f'(allow file-write* (subpath "{escaped}"))\n'
        "(allow process-exec)\n"
        "(allow process-fork)\n"
        "(allow signal)\n"
        "(allow sysctl-read)\n"
    )
