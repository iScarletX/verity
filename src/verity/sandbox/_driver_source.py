"""Trusted driver, staged verbatim into the sandbox tmpdir at run time.

IMPORTANT — this file is a TEMPLATE/ASSET, not something Verity's own
process imports. ``SandboxRunner`` reads this file's bytes from Verity's
own installed package and writes them, unmodified, to
``<tmpdir>/_sandboxdriver.py``. It never comes from or is derived from
the reviewed artifact. The driver itself is executed by a fresh
``python3`` subprocess wrapped by ``sandbox-exec`` (see ``runner.py`` /
``profile.py``); this module must therefore be a fully self-contained
script using only the standard library — it cannot import anything
from the ``verity`` package because it does not run with Verity's
``sys.path``.

What it does, in order:

1. Installs a ``sys.addaudithook`` that records a bounded set of
   filesystem / network / subprocess events (never full argv/env for
   subprocess-like events — only ``argv0`` + a capped preview, to avoid
   ever writing this process's real environment variables, which may
   contain live secrets, into the observation file).
1b. Wraps ``sqlite3.connect`` (see ``_install_sqlite3_instrumentation``)
   so raw SQL statement text reaching the stdlib sqlite3 driver is also
   recorded, bounded the same way. This one is deliberately NOT part of
   the audit hook above — CPython only added sqlite3's own audit events
   in Python 3.12, and this driver must work down to 3.9.
2. Sets ``sys.argv`` to ``[entry_point] + extra_argv`` (matching normal
   ``python3 entry_point.py ...`` semantics) and runs the reviewed
   script via ``runpy.run_path(entry_point, run_name="__main__")``.
3. Catches any exception OR ``SystemExit`` raised by the reviewed
   script so this driver process can still write its observation file
   before exiting with the same code the reviewed script would have
   produced.
4. Writes ``_verity_observation.json`` into its own directory (the
   sandbox tmpdir) with the fields the driver itself is responsible
   for; ``SandboxRunner`` fills in the process-level fields (exit code,
   signal, duration, peak memory) that only the parent can observe.
"""

from __future__ import annotations

import io
import json
import os
import runpy
import sys

_MAX_FILE_EVENTS = 500
_MAX_NETWORK_ATTEMPTS = 50
_MAX_SUBPROCESS_ATTEMPTS = 50
_MAX_SQL_ATTEMPTS = 50
_MAX_ARGV_PREVIEW_ITEMS = 20
_MAX_ARGV_ITEM_CHARS = 200
_MAX_PATH_CHARS = 4096
_MAX_SQL_STATEMENT_CHARS = 500
_MAX_EXC_MESSAGE_CHARS = 2000

_TMPDIR = os.path.dirname(os.path.realpath(__file__))
_OBSERVATION_PATH = os.path.join(_TMPDIR, "_verity_observation.json")

_file_events = []
_network_attempts = []
_subprocess_attempts = []
_sql_attempts = []
_truncated = {"fileEvents": False, "networkAttempts": False, "subprocessAttempts": False,
              "sqlAttempts": False}


def _inside_tmpdir(path) -> bool:
    try:
        real = os.path.realpath(str(path))
        return os.path.commonpath([real, _TMPDIR]) == _TMPDIR
    except (ValueError, OSError, TypeError):
        return False


def _record_file(op: str, path) -> None:
    p = str(path)[:_MAX_PATH_CHARS]
    if p == _OBSERVATION_PATH:
        # Don't let the driver's own bookkeeping write pollute the
        # observation it is producing.
        return
    if len(_file_events) >= _MAX_FILE_EVENTS:
        _truncated["fileEvents"] = True
        return
    _file_events.append({"op": op, "path": p, "insideSandbox": _inside_tmpdir(path)})


def _record_network(address) -> None:
    if len(_network_attempts) >= _MAX_NETWORK_ATTEMPTS:
        _truncated["networkAttempts"] = True
        return
    host = None
    port = None
    if isinstance(address, tuple) and len(address) >= 2:
        host, port = address[0], address[1]
    elif address is not None:
        host = str(address)[:_MAX_PATH_CHARS]
    # No network-allow clause exists in the sandbox profile, so every
    # attempt observed here was denied by the OS sandbox itself; this
    # field intentionally never becomes True in this design.
    _network_attempts.append({"host": host, "port": port, "allowed": False})


def _preview_argv(argv) -> list:
    out = []
    try:
        items = list(argv)
    except TypeError:
        items = [argv]
    for item in items[:_MAX_ARGV_PREVIEW_ITEMS]:
        out.append(str(item)[:_MAX_ARGV_ITEM_CHARS])
    return out


def _record_subprocess(argv0, argv) -> None:
    if len(_subprocess_attempts) >= _MAX_SUBPROCESS_ATTEMPTS:
        _truncated["subprocessAttempts"] = True
        return
    _subprocess_attempts.append({
        "argv0": str(argv0)[:_MAX_ARGV_ITEM_CHARS] if argv0 is not None else None,
        "argvPreview": _preview_argv(argv),
    })


def _record_sql(statement) -> None:
    if len(_sql_attempts) >= _MAX_SQL_ATTEMPTS:
        _truncated["sqlAttempts"] = True
        return
    _sql_attempts.append({"statement": str(statement)[:_MAX_SQL_STATEMENT_CHARS]})


def _install_sqlite3_instrumentation() -> None:
    """Best-effort only, and deliberately NOT built on ``sys.addaudithook``
    like every event above -- CPython only added sqlite3's own
    ``sqlite3.execute``/``executemany``/``executescript`` audit events in
    Python 3.12, and this driver must run on the same interpreter Verity
    itself supports (3.9+). Instead this wraps ``sqlite3.connect`` (an
    ordinary, patchable module-level function) so every ``Connection`` it
    returns is a subclass whose ``cursor()`` override hands back a
    ``Cursor`` subclass recording each statement's raw text before
    delegating to the real implementation. Subclassing ``sqlite3.Cursor``/
    ``sqlite3.Connection`` is permitted even though patching their methods
    directly is not (they are immutable C extension types). Connection-level
    ``execute``/``executemany``/``executescript`` (called without an
    explicit cursor) already route through ``self.cursor()`` internally, so
    overriding only the three ``Cursor`` methods observes every call site
    without double-counting. Never raises: if sqlite3 is unavailable or its
    shape ever changes, the reviewed script still runs normally with no SQL
    observation, identical to a script that never touches sqlite3 at all.
    """
    try:
        import sqlite3

        class _RecordingCursor(sqlite3.Cursor):
            def execute(self, sql, *args, **kwargs):
                _record_sql(sql)
                return super().execute(sql, *args, **kwargs)

            def executemany(self, sql, *args, **kwargs):
                _record_sql(sql)
                return super().executemany(sql, *args, **kwargs)

            def executescript(self, sql, *args, **kwargs):
                _record_sql(sql)
                return super().executescript(sql, *args, **kwargs)

        class _RecordingConnection(sqlite3.Connection):
            def cursor(self, *args, **kwargs):
                return super().cursor(_RecordingCursor)

        _real_connect = sqlite3.connect

        def _recording_connect(*args, **kwargs):
            kwargs["factory"] = _RecordingConnection
            return _real_connect(*args, **kwargs)

        sqlite3.connect = _recording_connect
    except Exception:
        pass


def _audit_hook(event: str, args: tuple) -> None:
    # NOTE: never index into ``args`` for a raw ``env`` mapping — several
    # of these events (subprocess.Popen, os.exec*, os.posix_spawn) carry
    # the full process environment as one argument, and this driver's own
    # environment is controlled by the trusted runner but may still
    # contain values we must not echo back into the observation file.
    try:
        if event == "open":
            # ``open``/``io.open`` pass a string mode (e.g. "w", "rb");
            # ``os.open`` passes ``mode=None`` and an integer flags
            # bitmask instead. Both forms are handled so a reviewed
            # script cannot dodge classification by calling ``os.open``
            # directly.
            path = args[0] if args else None
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else None
            is_write = isinstance(mode, str) and any(c in mode for c in "wax+")
            if not is_write and isinstance(flags, int):
                write_bits = (getattr(os, "O_WRONLY", 1) | getattr(os, "O_RDWR", 2)
                              | getattr(os, "O_APPEND", 0) | getattr(os, "O_CREAT", 0)
                              | getattr(os, "O_TRUNC", 0))
                is_write = bool(flags & write_bits)
            op = "write" if is_write else "read"
            _record_file(op, path)
        elif event == "socket.connect":
            address = args[1] if len(args) > 1 else None
            _record_network(address)
        elif event == "socket.__new__":
            # Socket creation alone does not reveal a destination; the
            # actual outbound attempt (with host/port) is captured at
            # socket.connect. Recording both would double-count the same
            # logical attempt, so this event is intentionally a no-op
            # beyond being explicitly listed as watched.
            pass
        elif event == "subprocess.Popen":
            executable = args[0] if args else None
            argv = args[1] if len(args) > 1 else []
            _record_subprocess(executable, argv)
        elif event == "os.system":
            command = args[0] if args else ""
            if isinstance(command, bytes):
                command = command.decode("utf-8", "replace")
            _record_subprocess("/bin/sh", ["-c", command])
        elif event in ("os.exec", "os.posix_spawn"):
            path = args[0] if args else None
            argv = args[1] if len(args) > 1 else []
            _record_subprocess(path, argv)
    except Exception:
        # The audit hook must never itself crash the interpreter or leak
        # a partially-formed record; swallow and drop this one event.
        pass


def _exception_payload(exc: BaseException):
    return {
        "type": type(exc).__name__,
        "message": str(exc)[:_MAX_EXC_MESSAGE_CHARS],
    }


def main() -> int:
    sys.addaudithook(_audit_hook)
    _install_sqlite3_instrumentation()

    if len(sys.argv) < 2:
        raised = {"type": "ValueError", "message": "no entry point supplied to driver"}
        _write_observation(raised, 1)
        return 1

    entry_point = sys.argv[1]
    extra_argv = sys.argv[2:]
    sys.argv = [entry_point] + list(extra_argv)

    raised = None
    exit_code = 0
    try:
        runpy.run_path(entry_point, run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            exit_code = 0
        elif isinstance(code, int):
            exit_code = code
        else:
            # A non-int argument to sys.exit() (e.g. a message string)
            # behaves like exit(1) with the value printed to stderr by
            # the interpreter; we mirror only the numeric contract here.
            sys.stderr.write(str(code) + "\n")
            exit_code = 1
    except BaseException as exc:  # noqa: BLE001 - deliberately broad
        raised = _exception_payload(exc)
        exit_code = 1

    _write_observation(raised, exit_code)
    return exit_code


def _write_observation(raised, exit_code) -> None:
    payload = {
        "raisedException": raised,
        "fileEvents": _file_events,
        "networkAttempts": _network_attempts,
        "subprocessAttempts": _subprocess_attempts,
        "sqlAttempts": _sql_attempts,
        "truncated": _truncated,
        "driverExitCode": exit_code,
    }
    try:
        with io.open(_OBSERVATION_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError:
        # If we cannot even write the observation file, the parent will
        # see a missing-file condition and surface status=failed.
        pass


if __name__ == "__main__":
    sys.exit(main())
