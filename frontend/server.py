"""Lightweight static server for the Lloyds Demo Web Application.

Serves frontend/public on FRONTEND_PORT (default 3000) with clean routes:
  /              -> index.html
  /transactions  -> transactions.html
  /mortgage      -> mortgage.html
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

PUBLIC_DIR = Path(__file__).resolve().parent / "public"
FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "3000"))

app = FastAPI(title="Lloyds Demo Web Application", docs_url=None, redoc_url=None)


@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/transactions", include_in_schema=False)
async def transactions_page():
    return FileResponse(PUBLIC_DIR / "transactions.html")


@app.get("/mortgage", include_in_schema=False)
async def mortgage_page():
    return FileResponse(PUBLIC_DIR / "mortgage.html")


app.mount("/", StaticFiles(directory=PUBLIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting Lloyds demo web app on port %s", FRONTEND_PORT)
    uvicorn.run(app, host="0.0.0.0", port=FRONTEND_PORT)
