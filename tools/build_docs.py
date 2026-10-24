"""Записывает docs/METHODOLOGY.md из core.methodology (тот же текст, что во вкладке интерфейса).

    python tools/build_docs.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import load_config  # noqa: E402
from core.methodology import markdown  # noqa: E402

if __name__ == "__main__":
    out = ROOT / "docs" / "METHODOLOGY.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(markdown(load_config()), encoding="utf-8")
    print("Записано:", out)
