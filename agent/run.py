"""Entry point to run the metadata capture agent server."""

import uvicorn


def main():
    uvicorn.run(
        "agent.server:app",
        host="localhost",
        port=8001,
        reload=True,
    )


if __name__ == "__main__":
    main()
