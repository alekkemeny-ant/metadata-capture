"""Entry point to run the metadata capture agent server."""

import logging
import os
import sys

import uvicorn

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)


def main():
    logger.info("agent.run starting (python=%s, deployment=%s)",
                sys.executable, os.environ.get("REPLIT_DEPLOYMENT", "0"))
    is_dev = os.environ.get("REPL_SLUG") is not None and os.environ.get("REPLIT_DEPLOYMENT") != "1"
    uvicorn.run(
        "agent.server:app",
        host="localhost",
        port=8001,
        reload=is_dev,
        reload_excludes=[
            "frontend/*",
            "*.log",
            ".local/*",
            "node_modules/*",
            ".git/*",
            "__pycache__/*",
            "*.pyc",
            "evals/*",
            ".pythonlibs/*",
        ] if is_dev else None,
    )


if __name__ == "__main__":
    main()
