# bhaptics-http
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19025338.svg)](https://doi.org/10.5281/zenodo.19025338)

**HTTP REST bridge for bHaptics haptic hardware.**

Wraps the bHaptics [`tact-python`](https://github.com/bhaptics/tact-python) SDK in a lightweight [aiohttp](https://docs.aiohttp.org/) server, exposing a language-agnostic REST API so that **any language or environment** can drive bHaptics vests, arm bands, gloves, and other devices over plain HTTP.

### Why?

bHaptics hardware is controlled through **bHaptics Player** (a Windows desktop app). The official SDK options are:

| SDK | Works in | Raw dot patterns? |
|-----|----------|------------------|
| tact-js | Browser only (WASM) | No (needs Studio) |
| tact-python | Windows Python | Yes — but no HTTP |
| bHaptics REST API | Via Player | No raw dot control |

**bhaptics-http fills the gap:** run it once on Windows alongside bHaptics Player, then call it from Node.js, WSL2, Docker, another machine — or any `curl` command.

The key feature is `/haptic/dot`: raw per-motor intensity control with **no bHaptics Studio pre-registration required**.

---

## Install

```bash
pip install bhaptics-http
```

> **Windows only** — must run on the same machine as bHaptics Player.

---

## Quick start

```bash
# Start bHaptics Player, then:
python -m bhaptics_http
```

```
bhaptics-http starting on 0.0.0.0:15883  (appId=BHapticsHTTP)
Endpoints:
  GET  http://0.0.0.0:15883/health
  POST http://0.0.0.0:15883/haptic
  POST http://0.0.0.0:15883/haptic/dot
  POST http://0.0.0.0:15883/haptic/stop
```

Test it:

```bash
curl http://localhost:15883/health
# {"ok": true, "backend": "bhaptics-python", "player": true}

curl -X POST http://localhost:15883/haptic/dot \
  -H 'Content-Type: application/json' \
  -d '{"deviceType":0,"duration":300,"motors":[{"index":0,"intensity":100},{"index":1,"intensity":100}]}'
# {"ok": true}
```

---

## REST API

### `GET /health`

Check connection to bHaptics Player.

```json
{"ok": true, "backend": "bhaptics-python", "player": true}
```

---

### `POST /haptic`

Play a named pattern registered in bHaptics Studio.

```json
{ "event": "HeartBeat", "deviceIndex": 0 }
```

---

### `POST /haptic/dot`

**Play raw per-motor intensities — no Studio required.**

```json
{
  "deviceType": 0,
  "duration":   300,
  "motors": [
    {"index": 0, "intensity": 100},
    {"index": 4, "intensity":  60}
  ]
}
```

The sparse `motors` request format is unchanged. The bridge expands it to a
zero-filled integer array before awaiting the SDK. For example, the request above
sets slots 0 and 4 in a 40-element vest array; every other slot is zero.
An empty or omitted `motors` list sends an all-zero array.

**Device layouts:**

| deviceType | Default layout | Default motorCount |
|-----------|----------------|--------------------|
| 0 | TactSuit X40 (legacy vest layout) | 40 |
| 1 / 2 | Tactosy arms, left / right | 6 |
| 3 | TactVisor | 4 |
| 4 / 5 | Tactosy hands, left / right | 3 |
| 6 / 7 | Tactosy feet, left / right | 3 |
| 8 / 9 | TactGlove, left / right | 8 |

Device position alone does not identify the hardware generation. Set optional
`motorCount` to the SDK layout size for your model, e.g. `32` for TactSuit Pro
or `3` for TactSleeve. This selects the array length; it does **not** remap motor
positions. Consult the manufacturer's [motor index guide](https://docs.bhaptics.com/sdk/further/motor)
for your model, particularly reduced-motor vests such as X16/Air. Defaults retain
the original X40/Tactosy focus; hardware-specific mapping must be checked on-device.

Validation runs before checking the Player connection:

- `deviceType`: integer `0`–`9`, default `0`.
- `duration`: integer milliseconds `1`–`2147483647`, default `100`.
  The SDK recommends at least 100 ms; shorter positive values remain accepted.
- `motorCount`: integer `1`–`40`, defaults from the table. The caller must select
  the layout appropriate to the hardware.
- `motors`: list of objects with integer `index` (`0` through `motorCount - 1`)
  and integer `intensity` (`0`–`100`). Indices must be unique.

Booleans, numeric strings, fractional numbers, missing motor fields, and values
outside these ranges return HTTP `400` with a JSON `error`. A failed Player
reconnection returns `503`; SDK exceptions or a playback result of `-1` return
`500`. A successful response remains `{"ok": true}` and confirms software dispatch,
not that a physical device actuated.

---

### `POST /haptic/stop`

Stop all currently playing haptic patterns.

```json
{"ok": true}
```

---

## Configuration

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `BHAPTICS_HTTP_PORT` | `15883` | Port to listen on |
| `BHAPTICS_APP_ID`   | `BHapticsHTTP` | App ID for bHaptics Player auth |
| `BHAPTICS_API_KEY`  | `""` | API key (usually empty for local Player) |

Or place a `tact-config.json` next to the server:

```json
{ "appId": "MyApp", "apiKey": "" }
```

---

## WSL2 / cross-machine setup

Run the server on **Windows**, then call it from WSL2 or another machine.

```bash
# Find your Windows host IP from WSL2:
cat /etc/resolv.conf | grep nameserver
# nameserver 172.22.112.1

# Then call:
curl http://172.22.112.1:15883/health
```

**Windows Firewall:** you may need to allow inbound TCP on port 15883:

```powershell
# PowerShell (as Administrator):
New-NetFirewallRule -DisplayName "bhaptics-http" -Direction Inbound `
  -Protocol TCP -LocalPort 15883 -Action Allow
```

Or simply run the server and the client on the same Windows machine.

---

## Node.js example

```js
const BASE = "http://localhost:15883";

// Raw dot pattern — vest upper row
await fetch(`${BASE}/haptic/dot`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    deviceType: 0,
    duration: 300,
    motors: [
      { index: 0, intensity: 100 },
      { index: 1, intensity: 100 },
      { index: 2, intensity: 100 },
      { index: 3, intensity: 100 },
    ],
  }),
});
```

See [`examples/client.js`](examples/client.js) for a full demo.

---

## Python library usage

```python
from aiohttp import web
from bhaptics_http.server import make_app, load_config

app_id, api_key = load_config()          # reads env / tact-config.json
app = make_app(app_id, api_key)
web.run_app(app, port=15883)
```

---

## CLI options

```
python -m bhaptics_http --help

options:
  --port PORT       Port (default: 15883, env: BHAPTICS_HTTP_PORT)
  --host HOST       Bind interface (default: 0.0.0.0)
  --config PATH     Path to tact-config.json
  --app-id ID       bHaptics appId
  --api-key KEY     bHaptics apiKey
```

---

## Development and verification

```bash
pip install -e ".[dev]"
python -m pytest -q
```

For hardware-independent tests on a host without the native SDK:

```bash
pip install aiohttp pytest pytest-asyncio
python -m pytest -q
```

Tests replace `bhaptics_python` with a mocked SDK and exercise real local HTTP
requests. They cover sparse conversion, layouts, validation, awaited calls,
SDK failures, reconnect, and application cleanup. Runtime warnings fail the tests.

Interface verification used the installed `bhaptics-python 0.2.2` CPython 3.12
Linux wheel. Its native functions return awaitable `asyncio.Future` objects,
although `inspect.iscoroutinefunction()` returns false. Passing motor dictionaries
to `play_dot` raises `TypeError`; an integer list passes native argument conversion.
The call signatures include `retry_initialize(app_id, api_key)`. See also the
[upstream example](https://github.com/bhaptics/tact-python/blob/main/python_test.py)
and [SDK reference](https://docs.bhaptics.com/sdk/python/references).
The minimum dependency version 0.2.0 was not runtime-tested.

All SDK operations run on the application's event loop. Library callers using
`init_bhaptics` directly must now use `await init_bhaptics(app_id, api_key)`.
`make_app()` remains synchronous; it awaits initialization on startup, cancels
its reconnect task on cleanup, and awaits SDK `close()`.

Physical verification still requires the target Windows host, bHaptics Player,
and the intended devices: check `/health` with Player running and stopped; pulse
the first and last valid slot at low intensity for each selected layout; verify
zero-filled slots, event playback, stop, and reconnection after restarting Player.
No physical device actuation or Windows integration was verified in these tests.

---

## Citation

If you use this in academic work, please cite via Zenodo:

> MissCrispenCakes (2026). *bhaptics-http: HTTP REST bridge for bHaptics haptic hardware* [Software]. Zenodo. https://doi.org/10.5281/zenodo.19025338

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19025338.svg)](https://doi.org/10.5281/zenodo.19025338)

---

## Related

- [tact-python](https://github.com/bhaptics/tact-python) — official Python SDK (this package wraps it)
- [bHaptics Developer Portal](https://developer.bhaptics.com/)
- [VR Ecology Project](https://ieeevr2026.headsetparties.com) — research context where this was first developed

---

## License

MIT © MissCrispenCakes
