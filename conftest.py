"""Гарантируем, что корень проекта в sys.path при запуске pytest из любой папки."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
