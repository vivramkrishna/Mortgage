"""Renders plugin/ai-plugin.json and plugin/openapi.yaml from their .template
counterparts by substituting {{PUBLIC_BASE_URL}} with the given base URL.

Run manually:
    python scripts/generate_plugin_manifest.py https://abcd1234.ngrok-free.app

Or import `generate(base_url)` from scripts/start.py once the ngrok tunnel is up.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugin"


def generate(base_url: str) -> None:
    base_url = base_url.rstrip("/")

    for template_name, output_name in [
        ("ai-plugin.template.json", "ai-plugin.json"),
        ("openapi.template.yaml", "openapi.yaml"),
    ]:
        template_path = PLUGIN_DIR / template_name
        output_path = PLUGIN_DIR / output_name
        content = template_path.read_text().replace("{{PUBLIC_BASE_URL}}", base_url)
        output_path.write_text(content)
        print(f"Generated {output_path} for base URL {base_url}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/generate_plugin_manifest.py <public_base_url>")
        sys.exit(1)
    generate(sys.argv[1])
