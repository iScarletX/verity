import os

filename = "report.txt"
with open(os.path.join("/data/reports", filename)) as handle:
    text = handle.read()
