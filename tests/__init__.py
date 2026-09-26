import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for p in (_ROOT / "vendor", _ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
