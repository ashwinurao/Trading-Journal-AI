"""One process for a home server: the API from main.py plus the built frontend.

    cd backend && python -m uvicorn server:app --host 0.0.0.0 --port 8771

Build the frontend first with REACT_APP_API_URL set to an empty string, so it calls
the API on the same address it was loaded from (see Dockerfile). FRONTEND_BUILD points
at the build folder (default ../frontend/build). For development, keep using
launch.bat: this file is only for running both halves behind one port.
"""
import os
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from main import app

BUILD = Path(os.getenv("FRONTEND_BUILD", Path(__file__).resolve().parent.parent / "frontend" / "build"))


async def _index(request):
    return FileResponse(BUILD / "index.html")


# main.py answers "/" with a health check; in server mode "/" is the app itself.
# For a health check use /api/update, which answers without touching the database.
app.router.routes.insert(0, Route("/", _index, include_in_schema=False))
app.mount("/", StaticFiles(directory=BUILD, html=True), name="frontend")
