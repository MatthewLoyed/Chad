"""Keeps `python -m evals.preflight ...` working now that preflight is a package."""

import sys

from evals.preflight.gate import main

if __name__ == "__main__":
    sys.exit(main())
