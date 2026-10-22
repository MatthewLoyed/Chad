import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Ensure CHAD_SERVER_URL points to dev brain without trailing spaces
os.environ["CHAD_SERVER_URL"] = "ws://localhost:8788/ws"

if __name__ == "__main__":
    from evals.runner import main
    sys.exit(main())
