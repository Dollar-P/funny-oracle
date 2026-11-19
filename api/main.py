"""FastAPI app (REDESIGN §2): REST + SSE for the Oracle Arena web UI.

    uvicorn api.main:app --port 8000          # serves web/dist too, if built
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import config  # noqa: E402
from api.routes import market, models, runs, scoreboard  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Funny Oracle API", version="1.0",
              description="Oracle Arena backend. For fun — not investment advice.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

for r in (models.router, market.router, runs.router, scoreboard.router):
    app.include_router(r, prefix="/api")


@app.get("/api/health")
def health():
    return {"ok": True, "mock": config.MOCK_LLM, "disclaimer": "For fun — not investment advice."}


DIST = ROOT / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        f = DIST / path
        if path and f.is_file() and DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html")  # client-side routes like /p/10
