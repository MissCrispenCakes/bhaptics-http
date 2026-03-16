# bhaptics-http

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

**Device type reference:**

| deviceType | Device |
|-----------|--------|
| 0 | TactSuit X16 / X40 (chest vest) |
| 1 | Tactosy2 left arm |
| 2 | Tactosy2 right arm |
| 3 | TactVisor head |
| 6 | Tactosy feet left |
| 7 | Tactosy feet right |
| 8 | TactGlove left |
| 9 | TactGlove right |

`intensity` range: `0`–`100`

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
