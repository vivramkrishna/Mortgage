"""Central configuration for the Lloyds Banking Demo Plugin backend."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Read .env from the repo root, so settings apply however this is launched —
# `python scripts/start.py`, a bare `uvicorn backend.app:app`, or an IDE runner.
# Real environment variables still win: load_dotenv does not override them.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BACKEND_HOST = os.getenv("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))

FRONTEND_HOST = os.getenv("FRONTEND_HOST", "localhost")
FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "3000"))
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", f"http://{FRONTEND_HOST}:{FRONTEND_PORT}")

# The bank's own service — owns customer records, underwriting and the
# internal Command Centre dashboard.
BANK_HOST = os.getenv("BANK_HOST", "localhost")
BANK_PORT = int(os.getenv("BANK_PORT", "4000"))
BANK_BASE_URL = os.getenv("BANK_BASE_URL", f"http://{BANK_HOST}:{BANK_PORT}")

# Public URL of the backend once tunnelled through ngrok. Set by scripts/start.py
# at runtime, or override manually via env var if ngrok is started separately.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://localhost:{BACKEND_PORT}")

PLUGIN_NAME = "Lloyds Banking Demo Plugin"
PLUGIN_NAME_MODEL = "lloyds_banking_demo"

# Which UI-resource MIME convention to advertise for the inline widget.
#   "mcp-app"   -> text/html;profile=mcp-app   (current MCP Apps standard)
#   "skybridge" -> text/html+skybridge         (older ChatGPT Apps SDK builds)
# Both metadata dialects are always emitted; only the MIME type differs, so
# flip this if your ChatGPT build renders text instead of the component.
WIDGET_MIME_MODE = os.getenv("WIDGET_MIME_MODE", "mcp-app")

WIDGET_MIME_TYPES = {
    "mcp-app": "text/html;profile=mcp-app",
    "skybridge": "text/html+skybridge",
}
WIDGET_MIME_TYPE = WIDGET_MIME_TYPES.get(WIDGET_MIME_MODE, WIDGET_MIME_TYPES["mcp-app"])

# --- OTP email delivery (used by the mortgage + transactions auth flow) -----
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USERNAME)
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

# Local-dev-only convenience: exposes the currently pending OTP via
# GET /api/_debug/otp so scripts/test_mcp.py can verify the flow without a
# real inbox. NEVER enable this in a real deployment.
DEBUG_EXPOSE_OTP = os.getenv("DEBUG_EXPOSE_OTP", "false").lower() == "true"
