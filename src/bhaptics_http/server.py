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
from contextlib import suppress
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

async def _is_connected() -> bool:
    try:
        return bool(await bh.is_connected())
    except Exception:
        return False


async def _try_reconnect() -> bool:
    """Attempt reconnect; returns True on success."""
    global _app_id, _api_key
    try:
        await bh.retry_initialize(_app_id, _api_key)
        if await _is_connected():
            log.info("[OK] Reconnected via retry_initialize()")
            return True
        await bh.registry_and_initialize(_app_id, _api_key, "")
        ok = await _is_connected()
        if ok:
            log.info("[OK] Reconnected via re-auth")
        return ok
    except Exception as exc:
        log.warning(f"[WARN] Reconnect failed: {exc}")
        return False


async def init_bhaptics(app_id: str, api_key: str) -> bool:
    """Initialise the SDK; returns True if connected."""
    global _app_id, _api_key
    _app_id, _api_key = app_id, api_key
    try:
        await bh.registry_and_initialize(app_id, api_key, "")
        if await _is_connected():
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
            connected = await _is_connected()
            if not connected:
                msg = "Lost connection" if was_connected else "Not connected"
                log.info(f"[...] {msg} — attempting reconnect")
                await _try_reconnect()
                was_connected = await _is_connected()
            else:
                was_connected = True
        except Exception as exc:
            log.error(f"[ERROR] reconnect_loop: {exc}")


# Default layouts: original X40 vest and Tactosy arms, current visor and accessories.
# Older/newer models sharing a position can override motorCount.
MOTOR_COUNTS = {0: 40, 1: 6, 2: 6, 3: 4, 4: 3, 5: 3, 6: 3, 7: 3, 8: 8, 9: 8}


def _integer(value, name: str, minimum: int, maximum: int) -> int:
    # bool is an int subclass, but is not a valid JSON motor value.
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"'{name}' must be an integer from {minimum} to {maximum}")
    return value


def _dot_values(body) -> tuple[int, int, list[int]]:
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")
    device_type = _integer(body.get("deviceType", 0), "deviceType", 0, 9)
    # Bound native signed 32-bit millisecond input without imposing a new time cap.
    duration = _integer(body.get("duration", 100), "duration", 1, 2**31 - 1)
    count = _integer(body.get("motorCount", MOTOR_COUNTS[device_type]), "motorCount", 1, 40)
    motors = body.get("motors", [])
    if not isinstance(motors, list):
        raise ValueError("'motors' must be a list of index/intensity objects")
    values = [0] * count
    seen = set()
    for motor in motors:
        if not isinstance(motor, dict):
            raise ValueError("Each motor must be an index/intensity object")
        index = _integer(motor.get("index"), "index", 0, count - 1)
        intensity = _integer(motor.get("intensity"), "intensity", 0, 100)
        if index in seen:
            raise ValueError(f"Duplicate motor index: {index}")
        seen.add(index)
        values[index] = intensity
    return device_type, duration, values


# ── Route handlers ────────────────────────────────────────────────────────────

async def handle_health(request: web.Request) -> web.Response:
    """
    GET /health

    Response:
        {"ok": bool, "backend": "bhaptics-python", "player": bool|null}
    """
    connected = await _is_connected()
    player = None
    if hasattr(bh, "is_bhaptics_player_running"):
        try:
            player = bool(await bh.is_bhaptics_player_running())
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

    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    event = body.get("event")
    if not event or not isinstance(event, str):
        return web.json_response({"error": "Missing or invalid 'event' key"}, status=400)

    if not await _is_connected():
        log.warning(f"[WARN] Not connected — reconnecting before play_event '{event}'")
        if not await _try_reconnect():
            return web.json_response({"error": "Not connected to bHaptics Player"}, status=503)

    try:
        device_index = int(body.get("deviceIndex", 0))
        result = await bh.play_event(event, device_index)
        if result == -1:
            raise RuntimeError("SDK rejected play_event")
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
            "motors": [             // sparse motor entries; omitted slots are zero
                {"index": 0, "intensity": 100},
                {"index": 4, "intensity": 50}
            ]
        }

    deviceType selects SDK position 0–9. Defaults are the legacy X40/Tactosy
    layouts; optional motorCount (1–40) selects another model's array length.
    See README for layout counts and motor indexing requirements.

    Response:
        {"ok": true}
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    try:
        device_type, duration, motors = _dot_values(body)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    if not await _is_connected():
        log.warning("[WARN] Not connected — reconnecting before play_dot")
        if not await _try_reconnect():
            return web.json_response({"error": "Not connected to bHaptics Player"}, status=503)

    try:
        result = await bh.play_dot(device_type, duration, motors)
        if result == -1:
            raise RuntimeError("SDK rejected play_dot")
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
        await bh.stop_all()
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
        await init_bhaptics(app_id, api_key)

    async def sdk_lifecycle(app: web.Application):
        task = asyncio.create_task(reconnect_loop())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await bh.close()

    app = web.Application()
    app.on_startup.append(on_startup)
    app.cleanup_ctx.append(sdk_lifecycle)
    app.router.add_get( "/health",       handle_health)
    app.router.add_post("/haptic",       handle_haptic)
    app.router.add_post("/haptic/dot",   handle_haptic_dot)
    app.router.add_post("/haptic/stop",  handle_haptic_stop)
    return app
