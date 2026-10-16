"""Vercel entrypoint: the FloodNet FastAPI app (backend/floodnet/api/main.py) as one Vercel Function.

The backend package lives in backend/; its data paths resolve from the repository root (floodnet/config.py), so the
package is imported from here rather than moved. Local development is unchanged:
    cd backend && .venv/Scripts/python -m uvicorn floodnet.api.main:app --port 8000
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from floodnet.api.main import app  # noqa: E402,F401
