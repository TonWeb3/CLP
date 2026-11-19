"""Start ClipForge:  python main.py"""
import os

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("CLIPFORGE_HOST", "0.0.0.0")
    port = int(os.environ.get("CLIPFORGE_PORT", "8120"))
    reload = os.environ.get("CLIPFORGE_RELOAD", "true").lower() in ("true", "1", "yes")
    print(f"\nClipForge is running at http://localhost:{port} (or http://your-ip:{port})\n")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload, log_level="info")
