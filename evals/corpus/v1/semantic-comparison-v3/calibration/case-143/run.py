from pathlib import Path

filename = "report.txt"
with open(Path("/data/reports") / filename) as handle:
    text = handle.read()
