# bhaptics-http — HTTP REST bridge for bHaptics haptic hardware
# Made by S.C. Vollmer · @MissCrispenCakes
# SPDX-FileCopyrightText: 2026 S.C. Vollmer
# SPDX-License-Identifier: MIT
# See LICENSE for permissions and terms; CITATION.cff for citation metadata.

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
import re
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

# Device watch (0.2.0). Player tells the SDK which devices are connected; the SDK keeps that
# table but nothing reported it. The reconnect loop now reads it every RECONNECT_INTERVAL and
# logs every change, and GET /devices serves the live table with name, status and battery.
POSITION_NAMES = {0: "vest", 1: "left arm", 2: "right arm", 3: "visor", 4: "left hand",
                  5: "right hand", 6: "left foot", 7: "right foot", 8: "left glove", 9: "right glove"}
_devices: dict = {"received": False, "list": [], "connected": None}   # connected: set of positions, None = no list yet
# Player 3.6.1 pushes its device table to an SDK client when a device CONNECTS and when the
# client registers, but not when a device disconnects (checked 2026-10-26 with a registered app
# id: a band switched off for 30 s produced nothing until it was switched on again, while
# re-registering every 10 s showed the disconnect at the next tick). So the watcher re-registers
# every DEVICE_POLL_S before reading the table. Each re-registration makes the SDK print its own
# reconnect lines to the console; set BHAPTICS_HTTP_DEVICE_POLL_S=0 to stop re-registering
# (then disconnects are only noticed when something else connects or the bridge restarts).
DEVICE_POLL_S = float(os.environ.get("BHAPTICS_HTTP_DEVICE_POLL_S", "10"))
_last_reregister = float("-inf")   # so the first call re-registers even just after boot

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
                if any(str(cfg.get(k, "")).startswith("YOUR_") for k in ("appId", "apiKey")):
                    log.warning(f"[WARN] {config_path} still has the placeholder values; running without a key")
                    cfg = {}
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


async def _player_running() -> Optional[bool]:
    """Is the Player process up? None when this SDK cannot tell (then the bridge tries anyway)."""
    if not hasattr(bh, "is_bhaptics_player_running"):
        return None
    try:
        return bool(await bh.is_bhaptics_player_running())
    except Exception:
        return None


# One SDK session at a time, and only the bridge reconnects. Each registration attempt made while
# Player is down leaves a socket (and an SDK retry loop) waiting; when Player starts they all
# connect in the same second, Player authenticates one, and every command on the others is
# dropped ("message blocked - is_auth=false" in Player's log). Seen 2026-11-03: bridge started
# before Player, and Player restarted mid-run; no buzz was felt and no disconnect was seen.
_reconnect_lock = asyncio.Lock()
_needs_fresh_session = False   # set after Player went away: register anew even if the SDK says connected


def _mark_fresh(needed: bool) -> None:
    global _needs_fresh_session
    _needs_fresh_session = needed


async def _close_session() -> None:
    """Drop the SDK's sockets and stop its own reconnect loop."""
    try:
        await bh.close()
    except Exception as exc:
        log.debug(f"close failed: {exc}")


async def _try_reconnect() -> bool:
    """Close whatever the SDK has open and register once; returns True on success.
    Does nothing while Player is not running."""
    async with _reconnect_lock:
        if await _is_connected() and not _needs_fresh_session:
            return True                      # another caller reconnected while we waited
        if await _player_running() is False:
            return False
        await _close_session()
        try:
            await bh.registry_and_initialize(_app_id, _api_key or "", "")
            ok = await _is_connected()
        except Exception as exc:
            log.warning(f"[WARN] Reconnect failed: {exc}")
            return False
        if ok:
            _mark_fresh(False)
            log.info("[OK] Reconnected to bHaptics Player")
        return ok


async def init_bhaptics(app_id: str, api_key: str) -> bool:
    """Initialise the SDK; returns True if connected. If Player is not running yet, waits for
    the reconnect loop instead of registering (see _reconnect_lock)."""
    global _app_id, _api_key
    _app_id, _api_key = app_id, api_key
    if await _player_running() is False:
        log.warning("[WAIT] bHaptics Player is not running — will connect when it starts")
        _mark_fresh(True)
        return False
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


async def _fetch_device_list() -> tuple[bool, list[dict]]:
    """Player's device table via the SDK's get_device_info_json(). Returns (received, list).
    received is False until Player has sent a list (the SDK returns "" before then, which with
    an unregistered app id can take seconds to minutes), or if the SDK cannot provide one."""
    if not hasattr(bh, "get_device_info_json"):
        return False, []
    try:
        raw = await asyncio.wait_for(bh.get_device_info_json(), timeout=1.0)
    except Exception:
        return False, []
    if not raw:
        return False, []
    try:
        items = json.loads(raw) if isinstance(raw, str) else list(raw)
    except Exception:
        return False, []
    out = []
    for d in items:
        if not isinstance(d, dict):
            continue
        pos = d.get("position")
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            continue
        out.append({
            "position":  pos,
            "name":      d.get("deviceName"),
            "role":      POSITION_NAMES.get(pos, f"position {pos}"),
            "connected": bool(d.get("connected")),
            "status":    d.get("connectionStatus"),
            "battery":   (d.get("battery") if isinstance(d.get("battery"), (int, float)) and d.get("battery") >= 0 else None),
        })
    return True, sorted(out, key=lambda x: x["position"])


async def reregister_for_devices(force: bool = False) -> None:
    """Ask Player for a fresh device table by re-registering the SDK session. Rate-limited to
    DEVICE_POLL_S unless forced; a no-op when polling is disabled or the SDK lacks the call."""
    global _last_reregister
    if not hasattr(bh, "retry_initialize") or _app_id is None:
        return
    if not _api_key and not force:
        return      # unregistered app id: Player answers "Cannot find the deployed workspace", no table
    import time as _time
    now = _time.monotonic()
    if not force and (DEVICE_POLL_S <= 0 or now - _last_reregister < DEVICE_POLL_S):
        return
    _last_reregister = now
    try:
        await asyncio.wait_for(bh.retry_initialize(_app_id, _api_key or ""), timeout=2.0)
        await asyncio.sleep(0.3)        # the table arrives just after the registration completes
    except Exception as exc:
        log.debug(f"[devices] re-register failed: {exc}")


async def refresh_devices(force_reregister: bool = False) -> dict:
    """Read Player's device table, remember it, and log every change in who is connected.
    Re-registers first (see reregister_for_devices) so disconnections are seen too."""
    if await _is_connected():
        await reregister_for_devices(force=force_reregister)
    received, items = await _fetch_device_list()
    if not received:
        return _devices
    now = {d["position"] for d in items if d["connected"]}
    prev = _devices["connected"]
    byname = {d["position"]: d for d in items}
    known  = {d["position"]: d for d in _devices["list"]}      # last table, for names Player drops
    def _desc(p: int) -> str:
        d = byname.get(p, {})
        name = d.get("name") or known.get(p, {}).get("name") or "?"
        batt = d.get("battery")
        # Player reports -1 (and sometimes no name) for a device that has just gone.
        batt_s = f", {batt} %" if isinstance(batt, (int, float)) and batt >= 0 else ""
        return f"{POSITION_NAMES.get(p, p)} ({name}{batt_s})"
    if prev is None:
        names = ", ".join(_desc(p) for p in sorted(now)) or "none"
        log.info(f"[devices] list received from Player — connected: {names}")
    else:
        for p in sorted(now - prev):
            log.info(f"[devices] {_desc(p)} connected")
        for p in sorted(prev - now):
            log.info(f"[devices] {_desc(p)} disconnected")
    _devices.update({"received": True, "list": items, "connected": now})
    return _devices


async def reconnect_loop() -> None:
    """Background task: reconnect whenever the SDK loses the Player connection, and watch the
    device table so connections and disconnections show in the console as they happen."""
    was_connected = False
    player_gone = False
    while True:
        await asyncio.sleep(RECONNECT_INTERVAL)
        try:
            if await _player_running() is False:
                if not player_gone:
                    log.info("[...] bHaptics Player is not running — waiting for it")
                    await _close_session()       # stop the SDK's own retries (see _reconnect_lock)
                    player_gone = True
                _mark_fresh(True)
                was_connected = False
                continue
            player_gone = False
            if _needs_fresh_session or not await _is_connected():
                msg = "Lost connection" if was_connected else "Not connected"
                log.info(f"[...] {msg} — attempting reconnect")
                was_connected = await _try_reconnect()
            else:
                was_connected = True
            await refresh_devices()
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
        raise ValueError("'motors' must be a list of index/intensity objects or of intensities")
    if motors and not any(isinstance(motor, dict) for motor in motors):
        # A plain list of intensities, one per motor slot, as sent by clients that
        # already hold a full array. It keeps its own length; no padding or trimming.
        if len(motors) > 40:
            raise ValueError("a plain 'motors' list can have at most 40 intensities")
        values = [_integer(v, f"motors[{i}]", 0, 100) for i, v in enumerate(motors)]
        if "motorCount" in body and count != len(values):
            raise ValueError("'motorCount' must equal the length of a plain 'motors' list")
        return device_type, duration, values
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


async def handle_devices(request: web.Request) -> web.Response:
    """
    GET /devices

    Which devices bHaptics Player currently reports as connected, by SDK
    position 0-9 (0 vest, 1/2 arms, 3 visor, 4/5 hands, 6/7 feet, 8/9 gloves).

    Response:
        {"ok": bool, "devices": {"0": bool, ..., "9": bool} | null, "present": [int, ...],
         "list": [{"position", "name", "role", "connected", "status", "battery"}, ...],
         "source": "player" | "flags", "pending": bool}

    Read live from Player's device table on every call (source "player"). Until Player has sent
    that table — with an unregistered app id that can take seconds to minutes — "pending" is
    true and the per-position SDK flags are used instead (source "flags"), which can be stale.
    "devices" is null if the installed SDK can report neither.

    ?refresh=1 re-registers with Player first, regardless of the poll interval, so the answer
    reflects a device switched off since the last tick.
    """
    connected = await _is_connected()
    force = (request.query.get("refresh") or "").lower() in ("1", "true", "yes")
    state = await refresh_devices(force_reregister=force)
    if state["received"]:
        items = state["list"]
        present = sorted(state["connected"])
        return web.json_response({
            "ok": connected,
            "devices": {str(p): (p in state["connected"]) for p in range(10)},
            "present": present,
            "list": items,
            "source": "player",
            "pending": False,
        })

    pending = hasattr(bh, "get_device_info_json")
    if not hasattr(bh, "is_bhaptics_device_connected"):
        return web.json_response({"ok": connected, "devices": None, "present": [], "list": [],
                                  "source": "flags", "pending": pending})

    async def one(position: int) -> bool:
        try:
            return bool(await asyncio.wait_for(bh.is_bhaptics_device_connected(position), timeout=1.0))
        except Exception:
            return False

    states = await asyncio.gather(*(one(p) for p in range(10)))
    return web.json_response({
        "ok": connected,
        "devices": {str(p): state_ for p, state_ in enumerate(states)},
        "present": [p for p, state_ in enumerate(states) if state_],
        "list": [],
        "source": "flags",
        "pending": pending,
    })


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

    "motors" may instead be a plain list of integer intensities (0-100), one
    per motor slot; it keeps its own length. The two forms cannot be mixed.

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


# ── Cross-origin reads ────────────────────────────────────────────────────────
# Only the read-only GET routes can be read by a web page, and only by pages served
# from this machine (the IEM demo runs on http://127.0.0.1:<port>). Add other origins
# with BHAPTICS_HTTP_CORS_ORIGINS (comma separated). The POST routes are unchanged:
# a page can still send them, but cannot read the reply.

READ_ONLY_PATHS = {"/health", "/devices"}
_LOCAL_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")


def _origin_allowed(origin: str) -> bool:
    extra = {o.strip() for o in os.environ.get("BHAPTICS_HTTP_CORS_ORIGINS", "").split(",") if o.strip()}
    return bool(_LOCAL_ORIGIN.match(origin)) or origin in extra


@web.middleware
async def read_only_cors(request: web.Request, handler):
    if request.path not in READ_ONLY_PATHS:
        return await handler(request)
    preflight = request.method == "OPTIONS"
    response = web.Response(status=204) if preflight else await handler(request)
    response.headers["Vary"] = "Origin"
    origin = request.headers.get("Origin")
    if origin and _origin_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        if preflight:
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = request.headers.get("Access-Control-Request-Headers", "")
            response.headers["Access-Control-Max-Age"] = "600"
    return response


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

    app = web.Application(middlewares=[read_only_cors])
    app.on_startup.append(on_startup)
    app.cleanup_ctx.append(sdk_lifecycle)
    app.router.add_get( "/health",       handle_health)
    app.router.add_get( "/devices",      handle_devices)
    app.router.add_post("/haptic",       handle_haptic)
    app.router.add_post("/haptic/dot",   handle_haptic_dot)
    app.router.add_post("/haptic/stop",  handle_haptic_stop)
    return app
