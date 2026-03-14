"""
Entry point for:  python -m bhaptics_http

Starts the bhaptics-http REST server.
All configuration is via environment variables or tact-config.json.
See `python -m bhaptics_http --help` for options.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from aiohttp import web

from bhaptics_http.server import make_app, load_config

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m bhaptics_http",
        description="bHaptics HTTP REST bridge — drive haptic hardware over HTTP",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=int(os.environ.get("BHAPTICS_HTTP_PORT", "15883")),
        help="Port to listen on (default: 15883, env: BHAPTICS_HTTP_PORT)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to tact-config.json (default: auto-detect)",
    )
    parser.add_argument(
        "--app-id",
        default=None,
        help="bHaptics appId (overrides env / config file)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="bHaptics apiKey (overrides env / config file)",
    )
    args = parser.parse_args()

    app_id, api_key = load_config(args.config)
    if args.app_id:
        app_id = args.app_id
    if args.api_key:
        api_key = args.api_key

    print(f"bhaptics-http starting on {args.host}:{args.port}  (appId={app_id})")
    print("Endpoints:")
    print(f"  GET  http://{args.host}:{args.port}/health")
    print(f"  POST http://{args.host}:{args.port}/haptic")
    print(f"  POST http://{args.host}:{args.port}/haptic/dot")
    print(f"  POST http://{args.host}:{args.port}/haptic/stop")

    app = make_app(app_id, api_key, config_path=args.config)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
