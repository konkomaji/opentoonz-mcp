"""Keep test data inside the repository folder unless OTMCP_WORK is set explicitly."""
import os
from pathlib import Path

os.environ.setdefault("OTMCP_WORK", str(Path(__file__).resolve().parents[1] / "work"))
