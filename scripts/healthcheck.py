"""Docker HEALTHCHECK: exits 0 when /health answers 200."""
import os
import sys
import urllib.request

port = os.getenv("PORT", "8080")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=4) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:  # noqa: BLE001
    sys.exit(1)
