#!/usr/bin/env python3
"""V2 Skill sandbox — explicit opt-in execution runner.

Executes one entry point from a local Skill folder inside a one-shot,
isolated macOS ``sandbox-exec`` sandbox (see ``src/verity/sandbox/``)
and reports what the script tried to do: files it read/wrote, network
connections it attempted (all denied by the sandbox profile), and
subprocesses it tried to spawn.

This is an EXPLICIT research/audit command, not part of the product
review path (``verity review`` / ``verity.cli``). It only runs when you
deliberately invoke it, and only on macOS with ``/usr/bin/sandbox-exec``
present — on any other platform it reports ``status=not_available``
and does not fall back to running the reviewed script unconfined.

Usage examples
--------------
# Run a script from a Skill folder under default budgets:
python3 tools/run_sandbox.py \\
    --input-dir path/to/skill \\
    --entry-point scripts/main.py

# Pass arguments through to the reviewed script, tighten budgets:
python3 tools/run_sandbox.py \\
    --input-dir path/to/skill \\
    --entry-point scripts/main.py \\
    --arg --dry-run --arg input.csv \\
    --cpu-seconds 5 --memory-mb 128 --wall-seconds 10 \\
    --out /tmp/sandbox_observation.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from verity.intake import IntakeError, intake_directory
from verity.sandbox.models import SandboxRunRequest
from verity.sandbox.runner import SandboxRunner


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="V2 Skill sandbox — observe a Skill entry point's runtime behaviour",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--input-dir", required=True,
                   help="Path to the local Skill folder to stage into the sandbox")
    p.add_argument("--entry-point", required=True,
                   help="Snapshot-relative path to the script to run (e.g. scripts/main.py)")
    p.add_argument("--arg", action="append", default=[], dest="args",
                   help="Argument to pass through to the entry point (repeatable)")
    p.add_argument("--cpu-seconds", type=int, default=10,
                   help="CPU-time budget in seconds (default: 10)")
    p.add_argument("--memory-mb", type=int, default=256,
                   help="RSS memory budget in MiB (default: 256)")
    p.add_argument("--wall-seconds", type=int, default=20,
                   help="Wall-clock budget in seconds (default: 20)")
    p.add_argument("--out", default="",
                   help="Write the full observation JSON to this path (default: print only)")
    args = p.parse_args(argv)

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"error: --input-dir is not a directory: {input_dir}", file=sys.stderr)
        return 1

    try:
        snapshot, file_bytes = intake_directory(str(input_dir))
    except IntakeError as exc:
        print(f"error: intake failed: {exc}", file=sys.stderr)
        return 1

    runner = SandboxRunner()
    if not runner.is_available():
        print("error: sandbox-exec is not available on this host "
              "(requires macOS with /usr/bin/sandbox-exec present); "
              "refusing to run the Skill unconfined.", file=sys.stderr)
        return 1

    request = SandboxRunRequest(
        entry_point=args.entry_point,
        argv=list(args.args),
        cpu_seconds=args.cpu_seconds,
        memory_mb=args.memory_mb,
        wall_seconds=args.wall_seconds,
    )

    print(f"[sandbox] input dir    : {input_dir}")
    print(f"[sandbox] entry point  : {args.entry_point}")
    print(f"[sandbox] cpu/mem/wall : {args.cpu_seconds}s / {args.memory_mb}MB / {args.wall_seconds}s")
    print()

    observation = runner.run(request, snapshot=snapshot, file_bytes=file_bytes)

    print(f"status              : {observation.status}"
          + (f" ({observation.reasonCode})" if observation.reasonCode else ""))
    print(f"isolationMechanism  : {observation.isolationMechanism}")
    print(f"exitCode            : {observation.exitCode}")
    print(f"terminatedBySignal  : {observation.terminatedBySignal}")
    print(f"durationSeconds     : {observation.durationSeconds}")
    print(f"peakMemoryMb        : {observation.peakMemoryMb}")
    if observation.raisedException:
        print(f"raisedException     : {observation.raisedException['type']}: "
              f"{observation.raisedException['message']}")
    print(f"fileEvents          : {len(observation.fileEvents)}"
          + (" (truncated)" if observation.truncated.get("fileEvents") else ""))
    print(f"networkAttempts     : {len(observation.networkAttempts)}"
          + (" (truncated)" if observation.truncated.get("networkAttempts") else ""))
    print(f"subprocessAttempts  : {len(observation.subprocessAttempts)}"
          + (" (truncated)" if observation.truncated.get("subprocessAttempts") else ""))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(asdict(observation), indent=2, ensure_ascii=False),
                             encoding="utf-8")
        print(f"\nwrote full observation: {out_path}")

    if observation.status in ("not_available", "no_entry_point", "failed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
