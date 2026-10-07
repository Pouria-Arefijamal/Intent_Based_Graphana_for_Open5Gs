"""Make `pytest` (not only `python -m pytest`) find the packages in the repo root and exporter/."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "exporter")):
    if p not in sys.path:
        sys.path.insert(0, p)
