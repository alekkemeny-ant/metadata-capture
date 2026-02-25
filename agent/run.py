"""Entry point to run the metadata capture agent server."""

import os

import uvicorn


def main():
    is_dev = os.environ.get("REPL_SLUG") is not None and os.environ.get("REPLIT_DEPLOYMENT") != "1"
    uvicorn.run(
        "agent.server:app",
        host="localhost",
        port=8001,
        reload=is_dev,
    )


if __name__ == "__main__":
    main()
