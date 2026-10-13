"""Start ClipForge:  python main.py"""
import os

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("CLIPFORGE_HOST", "127.0.0.1")
    port = int(os.environ.get("CLIPFORGE_PORT", "8000"))
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("CLIPFORGE_PASSWORD"):
        print("\n!! You bound ClipForge to a public address without CLIPFORGE_PASSWORD.\n"
              "!! Remote requests will be refused until you set a password.\n")
    print(f"\nClipForge is running at http://localhost:{port}\n")
    uvicorn.run("app.main:app", host=host, port=port, log_level="info")
