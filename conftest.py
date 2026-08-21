"""根级 conftest，确保项目根目录在 sys.path 中。"""
import sys
from pathlib import Path

_root = str(Path(__file__).parent)
if _root not in sys.path:
    sys.path.insert(0, _root)
