"""Vercel serverless entry point — wraps the FastAPI app."""

import sys
from pathlib import Path

# Add Backend directory to Python path so imports like `from app.main` work
backend_dir = Path(__file__).resolve().parent.parent / "Backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.main import app  # noqa: E402

# Vercel picks up the `app` variable as the ASGI application
