"""Entry point for python -m sdr_mcp."""

import argparse
import os
import sys

from sdr_mcp.server import mcp, set_active_backend


def main():
    parser = argparse.ArgumentParser(description="Universal SDR MCP Server")
    parser.add_argument(
        "--backend",
        choices=["sdrpp", "mock"],
        default=os.environ.get("SDR_BACKEND", "sdrpp"),
        help="Active SDR backend (default: sdrpp)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE transport (default: 8000)",
    )

    args = parser.parse_args()
    set_active_backend(args.backend)

    if args.transport == "sse":
        mcp.run(transport="sse", port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
