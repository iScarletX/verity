#!/usr/bin/env python3
"""Disabled V2 Skill research command.

This compatibility entry point intentionally never executes reviewed code.
The underlying prototype allows host-wide reads, process/fork escape patterns,
and unbounded descendant output/disk behavior, so macOS ``sandbox-exec`` alone
is not a sufficient product isolation boundary. Every invocation fails closed
with ``sandbox_isolation_hardening_required`` until V2 is rebuilt around a
separately reviewed container or microVM boundary.
"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Disabled V2 Skill research command; every invocation returns "
            "sandbox_isolation_hardening_required without executing code"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--input-dir", required=True,
                   help="Legacy compatibility value; never read or executed")
    p.add_argument("--entry-point", required=True,
                   help="Legacy compatibility value; never executed")
    p.add_argument("--arg", action="append", default=[], dest="args",
                   help="Legacy compatibility value; never passed to code")
    p.add_argument("--cpu-seconds", type=int, default=10,
                   help="Legacy compatibility value; no runtime is started")
    p.add_argument("--memory-mb", type=int, default=256,
                   help="Legacy compatibility value; no runtime is started")
    p.add_argument("--wall-seconds", type=int, default=20,
                   help="Legacy compatibility value; no runtime is started")
    p.add_argument("--out", default="",
                   help="Legacy compatibility value; no output file is created")
    # Parse legacy flags only to produce a stable, controlled refusal. Do not
    # inspect the input directory, construct the prototype runner, or create
    # the requested output file.
    p.parse_args(argv)
    print(
        "error: sandbox_isolation_hardening_required: Skill execution is "
        "unavailable until the V2 isolation boundary is hardened.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
