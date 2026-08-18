#!/usr/bin/env python3
"""Single-command launcher for the Lloyds Banking Demo Plugin.

Starts:
  1. The FastAPI backend (REST + MCP Streamable HTTP)  -> BACKEND_PORT (default 8000)
  2. The Lloyds demo web application (static frontend)  -> FRONTEND_PORT (default 3000)
  3. An ngrok tunnel exposing the backend publicly (unless --no-ngrok)

After the ngrok tunnel is up, it regenerates plugin/ai-plugin.json and
plugin/openapi.yaml with the live public URL so they're ready to hand to
ChatGPT immediately.

Usage:
    python scripts/start.py               # backend + frontend + ngrok
    python scripts/start.py --no-ngrok    # backend + frontend only (local testing)
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env before anything reads os.getenv, so the values reach both this
# launcher and every service it spawns (they inherit os.environ). Without this
# a .env file is inert — which is how SMTP settings silently went missing.
from dotenv import load_dotenv  # noqa: E402  (must run before the getenv calls)

load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("start")


def wait_until_ready(url: str, proc: subprocess.Popen, timeout: float = 20.0) -> bool:
    """Poll `url` until it answers, the process dies, or we time out."""
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status < 500:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    return False


# The agent's local API normally sits on 4040, but moves to the next free port
# when a previous agent already owns it.
NGROK_API_PORTS = (4040, 4041, 4042)


def existing_ngrok_tunnel(backend_port: int) -> str | None:
    """Public URL of an ngrok tunnel already forwarding to `backend_port`.

    ngrok's free tier allows a single online endpoint on the reserved domain,
    so an agent left over from a previous run blocks any new tunnel outright.
    When that leftover already points at our backend, reusing it beats
    silently dropping back to a localhost URL that ChatGPT cannot reach.
    """
    import json
    import urllib.error
    import urllib.request

    for api_port in NGROK_API_PORTS:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{api_port}/api/tunnels", timeout=1
            ) as response:
                payload = json.load(response)
        except (urllib.error.URLError, OSError, ValueError):
            continue

        for tunnel in payload.get("tunnels", []):
            addr = str(tunnel.get("config", {}).get("addr", ""))
            public_url = str(tunnel.get("public_url", ""))
            if addr.endswith(f":{backend_port}") and public_url.startswith("https://"):
                return public_url
    return None


def port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return True
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-ngrok", action="store_true", help="Skip the ngrok tunnel")
    parser.add_argument("--backend-port", type=int, default=int(os.getenv("BACKEND_PORT", "8000")))
    parser.add_argument("--frontend-port", type=int, default=int(os.getenv("FRONTEND_PORT", "3000")))
    parser.add_argument("--bank-port", type=int, default=int(os.getenv("BANK_PORT", "4000")))
    args = parser.parse_args()

    backend_port = args.backend_port
    frontend_port = args.frontend_port
    bank_port = args.bank_port

    for label, port in [
        ("backend", backend_port),
        ("frontend", frontend_port),
        ("bank", bank_port),
    ]:
        if port_in_use(port):
            logger.error(
                "Port %s is already in use, so the %s server cannot start there. "
                "Free it up or pick a different port, e.g.: "
                "python scripts/start.py --%s-port <other-port>",
                port,
                label,
                label,
            )
            sys.exit(1)

    env = os.environ.copy()
    env["BACKEND_PORT"] = str(backend_port)
    env["FRONTEND_PORT"] = str(frontend_port)
    env["BANK_PORT"] = str(bank_port)
    env["FRONTEND_BASE_URL"] = env.get("FRONTEND_BASE_URL", f"http://localhost:{frontend_port}")
    env["BANK_BASE_URL"] = env.get("BANK_BASE_URL", f"http://localhost:{bank_port}")
    env.setdefault("PYTHONUNBUFFERED", "1")

    processes: list[subprocess.Popen] = []

    # Whether *we* started an ngrok agent, tracked separately from
    # args.no_ngrok: a failed tunnel flips that flag, but pyngrok has already
    # spawned the agent by then and it still needs cleaning up.
    ngrok_spawned = False

    def shutdown(*_args):
        logger.info("Shutting down all processes...")
        for p in processes:
            if p.poll() is None:
                p.terminate()
        for p in processes:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        # Kill the agent even if the tunnel failed — an agent left running is
        # exactly what holds the reserved domain and blocks the next run.
        if ngrok_spawned:
            try:
                from pyngrok import ngrok

                ngrok.kill()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # The bank core starts first: the backend calls it for customer lookups
    # and underwriting.
    logger.info("Starting Lloyds bank core on port %s", bank_port)
    bank_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "bank.app:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(bank_port),
        ],
        cwd=ROOT,
        env=env,
    )
    processes.append(bank_proc)

    logger.info("Starting Lloyds backend on port %s", backend_port)
    backend_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(backend_port),
        ],
        cwd=ROOT,
        env=env,
    )
    processes.append(backend_proc)

    logger.info("Starting Lloyds demo web app on port %s", frontend_port)
    frontend_proc = subprocess.Popen(
        [sys.executable, "frontend/server.py"],
        cwd=ROOT,
        env=env,
    )
    processes.append(frontend_proc)

    # Don't advertise anything until both services actually answer, so the
    # summary banner below can never report a service that failed to start.
    for label, url, proc in [
        ("bank", f"http://localhost:{bank_port}/health", bank_proc),
        ("backend", f"http://localhost:{backend_port}/health", backend_proc),
        ("frontend", f"http://localhost:{frontend_port}/", frontend_proc),
    ]:
        if not wait_until_ready(url, proc):
            logger.error(
                "The %s server failed to start (see the traceback above). Aborting.", label
            )
            shutdown()
        logger.info("%s is ready", label.capitalize())

    public_url = f"http://localhost:{backend_port}"
    if not args.no_ngrok:
        reused_url = existing_ngrok_tunnel(backend_port)
        if reused_url:
            public_url = reused_url
            logger.info(
                "Reusing the ngrok tunnel already serving port %s: %s",
                backend_port,
                public_url,
            )
        else:
            try:
                import shutil

                from pyngrok import conf, ngrok

                system_ngrok = shutil.which("ngrok")
                pyngrok_config = (
                    conf.PyngrokConfig(ngrok_path=system_ngrok) if system_ngrok else None
                )

                logger.info("Opening ngrok tunnel to backend port %s...", backend_port)
                # connect() spawns the agent, so from here it needs cleanup on
                # exit whether or not the tunnel itself succeeds.
                ngrok_spawned = True
                tunnel = ngrok.connect(backend_port, "http", pyngrok_config=pyngrok_config)
                public_url = tunnel.public_url
                logger.info("ngrok tunnel established: %s", public_url)
            except Exception as exc:
                detail = str(exc)
                if "ERR_NGROK_334" in detail or "already online" in detail:
                    remedy = (
                        "An ngrok agent from an earlier run is still holding your "
                        "reserved domain. Stop it and try again:\n"
                        "      pkill -f 'ngrok start'"
                    )
                else:
                    remedy = (
                        "Ensure `ngrok config add-authtoken <token>` has been run. "
                        "See README.md for setup instructions."
                    )
                logger.error(
                    "Failed to start ngrok tunnel — falling back to localhost-only "
                    "mode, which ChatGPT cannot reach.\n      %s\n      Details: %s",
                    remedy,
                    detail,
                )
                args.no_ngrok = True

    from scripts.generate_plugin_manifest import generate

    generate(public_url)

    print("\n" + "=" * 72)
    print(f"  {'Lloyds Banking Demo Plugin':^68}")
    print("=" * 72)
    print(f"  Backend (local):     http://localhost:{backend_port}")
    print(f"  Backend (public):    {public_url}")
    print(f"  MCP endpoint:        {public_url}/mcp")
    print(f"  OpenAPI spec:        {public_url}/openapi.yaml")
    print(f"  Plugin manifest:     {public_url}/.well-known/ai-plugin.json")
    print(f"  Lloyds web app:      http://localhost:{frontend_port}")
    print(f"  Bank Command Centre: http://localhost:{bank_port}")
    print(f"  API docs (Swagger):  http://localhost:{backend_port}/docs")
    print("=" * 72)
    print("  Connect to ChatGPT:")
    print("   1. ChatGPT -> Settings -> Connectors -> Create -> paste the MCP endpoint above")
    print("      (or, for legacy Actions: paste the OpenAPI spec URL above)")
    print("   2. Try: \"Show my transactions\"  or  \"I want a mortgage\"")
    print("=" * 72 + "\n")
    print("Press Ctrl+C to stop all services.\n")

    try:
        while True:
            time.sleep(1)
            for p in processes:
                if p.poll() is not None:
                    logger.error("Process %s exited unexpectedly (code %s)", p.args[0:2], p.returncode)
                    shutdown()
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
