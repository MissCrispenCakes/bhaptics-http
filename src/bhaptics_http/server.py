"""
bhaptics_http.server
~~~~~~~~~~~~~~~~~~~~

Lightweight aiohttp HTTP server that wraps the bhaptics-python SDK,
exposing a language-agnostic REST interface for bHaptics hardware.

Useful when:
  - You are running in an environment where tact-js (browser WASM) is
    unavailable (Node.js server, Python script, WSL2, Docker, CI).
  - You want to drive raw dot/motor patterns without pre-registering
    .tact files in bHaptics Studio.
  - You need a single Windows-side process that any language can call
    over HTTP.

Usage (standalone):
    pip install bhaptics-http
    python -m bhaptics_http            # default port 15883
    python -m bhaptics_http --port 8080

Usage (library):
    from bhaptics_http.server import make_app
    app = make_app(app_id="MyApp", api_key="")
    # pass to aiohttp.web.run_app(app, port=15883)

Environment variables:
    BHAPTICS_APP_ID       Override appId  (default: from tact-config.json or "BHapticsHTTP")
    BHAPTICS_API_KEY      Override apiKey (default: from tact-config.json or "")
    BHAPTICS_HTTP_PORT    Port to listen on (default: 15883)

REST endpoints:
    GET  /health          Connection status
    POST /haptic          Play a named Studio event
    POST /haptic/dot      Play raw motor array (no Studio required)
    POST /haptic/stop     Stop all haptics
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from aiohttp import web

# ── bhaptics-python import ────────────────────────────────────────────────────
try:
    import bhaptics_python as bh
except ImportError:
    print(
        "[ERROR] bhaptics-python not installed.\n"
        "        Run:  pip install bhaptics-python\n"
        "        Docs: https://github.com/bhaptics/bhaptics-python"
    )
    sys.exit(1)

log = logging.getLogger("bhaptics_http")

# ── Module-level credential cache ────────────────────────────────────────────
_app_id: Optional[str] = None
_api_key: Optional[str] = None

RECONNECT_INTERVAL = 5  # seconds between liveness checks

# ── Config helpers ────────────────────────────────────────────────────────────

def load_config(config_path: Optional[Path] = None) -> tuple[str, str]:
    """
    Resolve appId / apiKey from (in priority order):
      1. Environment variables BHAPTICS_APP_ID / BHAPTICS_API_KEY
      2. tact-config.json at *config_path* (if provided or auto-detected)
      3. Built-in defaults ("BHapticsHTTP", "")
    """
    app_id  = os.environ.get("BHAPTICS_APP_ID")
    api_key = os.environ.get("BHAPTICS_API_KEY")

    if not app_id or not api_key:
        if config_path is None:
            # Look next to this file, then cwd
            candidates = [
                Path(__file__).parent / "tact-config.json",
                Path.cwd() / "tact-config.json",
            ]
            config_path = next((p for p in candidates if p.exists()), None)

        if config_path and config_path.exists():
            try:
                cfg = json.loads(config_path.read_text())
                app_id  = app_id  or cfg.get("appId",  "BHapticsHTTP")
                api_key = api_key or cfg.get("apiKey", "")
                log.info(f"Loaded config from {config_path}")
            except Exception as exc:
                log.warning(f"Could not parse {config_path}: {exc} — using defaults")

    return (app_id or "BHapticsHTTP"), (api_key or "")


# ── SDK connection helpers ────────────────────────────────────────────────────

def _is_connected() -> bool:
    try:
        return bool(bh.is_connected())
    except Exception:
        return False


def _try_reconnect() -> bool:
    """Attempt reconnect; returns True on success."""
    global _app_id, _api_key
    try:
        bh.retry_initialize()
        if _is_connected():
            log.info("[OK] Reconnected via retry_initialize()")
            return True
        bh.registry_and_initialize(_app_id, _api_key, "BHapticsHTTP")
        ok = _is_connected()
        if ok:
            log.info("[OK] Reconnected via re-auth")
        return ok
    except Exception as exc:
        log.warning(f"[WARN] Reconnect failed: {exc}")
        return False


def init_bhaptics(app_id: str, api_key: str) -> bool:
    """Initialise the SDK; returns True if connected."""
    global _app_id, _api_key
    _app_id, _api_key = app_id, api_key
    try:
        bh.registry_and_initialize(app_id, api_key, "BHapticsHTTP")
        if _is_connected():
            log.info(f"[OK] bhaptics-python connected (appId={app_id})")
            return True
        log.warning("[WARN] Initialised but not yet connected — is bHaptics Player running?")
        return False
    except Exception as exc:
        log.error(f"[ERROR] Init failed: {exc}")
        return False


async def reconnect_loop() -> None:
    """Background task: reconnect whenever the SDK loses the Player connection."""
    was_connected = False
    while True:
        await asyncio.sleep(RECONNECT_INTERVAL)
        try:
            connected = _is_connected()
            if not connected:
                msg = "Lost connection" if was_connected else "Not connected"
                log.info(f"[...] {msg} — attempting reconnect")
                _try_reconnect()
                was_connected = _is_connected()
            else:
                was_connected = True
        except Exception as exc:
            log.error(f"[ERROR] reconnect_loop: {exc}")


# ── Route handlers ────────────────────────────────────────────────────────────

async def handle_health(request: web.Request) -> web.Response:
    """
    GET /health

    Response:
        {"ok": bool, "backend": "bhaptics-python", "player": bool|null}
    """
    connected = _is_connected()
    player = None
    if hasattr(bh, "is_bhaptics_player_running"):
        try:
            result = bh.is_bhaptics_player_running()
            player = bool(await result) if asyncio.isfuture(result) else bool(result)
        except Exception:
            player = None
    return web.json_response({"ok": connected, "backend": "bhaptics-python", "player": player})


async def handle_haptic(request: web.Request) -> web.Response:
    """
    POST /haptic

    Play a named tact event previously registered in bHaptics Studio.

    Body:
        {"event": "PatternName", "deviceIndex": 0}

    Response:
        {"ok": true, "event": "PatternName"}
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    event = body.get("event")
    if not event or not isinstance(event, str):
        return web.json_response({"error": "Missing or invalid 'event' key"}, status=400)

    if not _is_connected():
        log.warning(f"[WARN] Not connected — reconnecting before play_event '{event}'")
        if not _try_reconnect():
            return web.json_response({"error": "Not connected to bHaptics Player"}, status=503)

    try:
        device_index = int(body.get("deviceIndex", 0))
        bh.play_event(event, device_index)
        log.info(f"[>] play_event: {event}")
        return web.json_response({"ok": True, "event": event})
    except Exception as exc:
        log.error(f"[ERROR] play_event '{event}': {exc}")
        return web.json_response({"error": str(exc)}, status=500)


async def handle_haptic_dot(request: web.Request) -> web.Response:
    """
    POST /haptic/dot

    Play raw motor intensities without a Studio-registered pattern.
    This is the key endpoint that bHaptics Player's own REST API does not expose.

    Body:
        {
            "deviceType": 0,        // 0=vest, 1=left arm, 2=right arm, ...
            "duration":   100,      // milliseconds
            "motors": [             // one entry per motor slot
                {"index": 0, "intensity": 100},
                {"index": 4, "intensity": 50}
            ]
        }

    Device type reference (bHaptics TactSuit family):
        0  = TactSuit X16/X40 (chest vest)
        1  = Tactosy2 left arm
        2  = Tactosy2 right arm
        3  = TactVisor head
        6  = Tactosy feet left
        7  = Tactosy feet right
        8  = TactGlove left
        9  = TactGlove right

    Response:
        {"ok": true}
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    if not _is_connected():
        log.warning("[WARN] Not connected — reconnecting before play_dot")
        if not _try_reconnect():
            return web.json_response({"error": "Not connected to bHaptics Player"}, status=503)

    try:
        device_type = int(body.get("deviceType", 0))
        duration    = int(body.get("duration",   100))
        motors      = body.get("motors", [])
        bh.play_dot(device_type, duration, motors)
        log.info(f"[>] play_dot device={device_type} duration={duration}ms motors={len(motors)}")
        return web.json_response({"ok": True})
    except Exception as exc:
        log.error(f"[ERROR] play_dot: {exc}")
        return web.json_response({"error": str(exc)}, status=500)


async def handle_haptic_stop(request: web.Request) -> web.Response:
    """
    POST /haptic/stop

    Stop all currently playing haptic patterns.

    Response:
        {"ok": true}
    """
    try:
        bh.stop_all()
        log.info("[>] stop_all")
        return web.json_response({"ok": True})
    except Exception as exc:
        log.error(f"[ERROR] stop_all: {exc}")
        return web.json_response({"error": str(exc)}, status=500)


# ── App factory ───────────────────────────────────────────────────────────────

def make_app(
    app_id: str,
    api_key: str,
    *,
    config_path: Optional[Path] = None,
) -> web.Application:
    """
    Create and return the aiohttp Application.

    Startup connects to bHaptics Player and launches the background
    reconnect loop.  Pass the returned app to ``aiohttp.web.run_app()``.
    """
    async def on_startup(app: web.Application) -> None:
        init_bhaptics(app_id, api_key)
        asyncio.ensure_future(reconnect_loop())

    app = web.Application()
    app.on_startup.append(on_startup)
    app.router.add_get( "/health",       handle_health)
    app.router.add_post("/haptic",       handle_haptic)
    app.router.add_post("/haptic/dot",   handle_haptic_dot)
    app.router.add_post("/haptic/stop",  handle_haptic_stop)
    return app
