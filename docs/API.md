# bhaptics-http reference

Everything the README leaves out.

## Why?

bHaptics hardware is controlled through **bHaptics Player** (a Windows desktop app). The official SDK options are:

| SDK | Works in | Raw dot patterns? |
|-----|----------|------------------|
| tact-js | Browser only (WASM) | No (needs Studio) |
| tact-python | Windows Python | Yes — but no HTTP |
| bHaptics REST API | Via Player | No raw dot control |
| Player OSC (Lab, 3.6.9+) | Anything that sends UDP, not browsers | Yes, one motor per message |

**bhaptics-http fills the gap:** run it once on Windows alongside bHaptics Player, then call it from Node.js, WSL2, Docker, another machine — or any `curl` command.

The key feature is `/haptic/dot`: raw per-motor intensity control with **no bHaptics Studio pre-registration required**.

## Compatibility

- **bHaptics Player** must be running on the same machine as the bridge. It is a Windows desktop application.
- **SDK:** `bhaptics-python` 0.2.2 or newer. With 0.2.0, `is_connected()` stays false even when Player has accepted the connection, so the bridge reports `"ok": false` and every haptic request fails with 503. `pip install --upgrade bhaptics-python` fixes it. Every SDK call is asynchronous, and the bridge awaits each one. As bHaptics' documentation instructs, the SDK's third `registry_and_initialize` argument is passed as an empty string.
- **Python:** 3.9 or newer. The SDK publishes wheels for CPython 3.8 to 3.13 on Windows (x86-64), macOS (x86-64 and arm64) and Linux (x86-64), and no source package, so other platforms cannot install it.
- The interface was checked against the real SDK, versions 0.2.0 and 0.2.2 (return types and argument handling), and against bHaptics' [Python reference](https://docs.bhaptics.com/sdk/python/references/). On hardware (bHaptics Player 3.6.1, October 2026), only 0.2.2 reported a working connection.
Checked again on Player 3.6.9 (November 2026) with a TactSuit X16 and Tactosy2 arm bands, with
and without a key, including the bridge started before Player and Player restarted.

## Install notes

**One name, if pip's `Scripts` folder is on your PATH.** pip installs a `bhaptics-http` command,
so you install, upgrade and run with the same name:

```bash
pip install --upgrade bhaptics-http
bhaptics-http
```

If the command is not found, pip's `Scripts` folder is not on your PATH (pip warns about this at
install time). Use `python -m bhaptics_http` instead. Note the underscore: Python module names
cannot contain a hyphen, so `python -m bhaptics-http` fails with "No module named bhaptics-http".

On start, the bridge prints:

```
bhaptics-http starting on 0.0.0.0:15883  (appId=BHapticsHTTP)
Endpoints:
  GET  http://0.0.0.0:15883/health
  GET  http://0.0.0.0:15883/devices
  POST http://0.0.0.0:15883/haptic
  POST http://0.0.0.0:15883/haptic/dot
  POST http://0.0.0.0:15883/haptic/stop
```

Replies to the Quick start commands:

```bash
curl http://localhost:15883/health
# {"ok": true, "backend": "bhaptics-python", "player": true}

curl -X POST http://localhost:15883/haptic/dot \
  -H 'Content-Type: application/json' \
  -d '{"deviceType":0,"duration":300,"motors":[{"index":0,"intensity":100},{"index":1,"intensity":100}]}'
# {"ok": true}
```

## REST API

### `GET /health`

Check connection to bHaptics Player.

```json
{"ok": true, "backend": "bhaptics-python", "player": true}
```

---

### `GET /devices`

Which bHaptics devices the Player currently reports as connected, by SDK position
(0 vest, 1 and 2 arms, 3 visor, 4 and 5 hands, 6 and 7 feet, 8 and 9 gloves), read live from
Player's device table on every call. A client can use it to offer only the hardware that is
actually being worn.

```json
{"ok": true, "present": [1, 2], "source": "player", "pending": false,
 "devices": {"0": false, "1": true, "2": true, "3": false, "4": false,
             "5": false, "6": false, "7": false, "8": false, "9": false},
 "list": [
   {"position": 0, "name": "TactSuitX16", "role": "vest", "connected": false,
    "status": "Disconnected", "battery": 100},
   {"position": 1, "name": "Tactosy2_V3 (L)", "role": "left arm", "connected": true,
    "status": "Connected", "battery": 98},
   {"position": 2, "name": "Tactosy2_V3 (R)", "role": "right arm", "connected": true,
    "status": "Connected", "battery": 99}]}
```

`pending` is `true` until Player has sent its device table to this bridge. With a registered app
id and key that is within a second of starting. With no key, Player sends the table only when a
device switches on while the bridge is running, so devices already on when it starts stay
`pending` (tested with Player 3.6.9). While pending, `devices` and `present` come from the SDK's per-position flags
(`"source": "flags"`), which can be stale after a device leaves. `devices` is `null` if the
installed SDK can report neither.

The bridge also watches the table and logs every change, so the console shows
`[devices] left arm (Tactosy2_V3 (L), 98 %) connected` or `… disconnected`.

**How disconnections are noticed.** Player 3.6.1 pushes its device table to an SDK client when
a device connects and when the client registers, but not when a device disconnects (checked on
hardware, 2026-10-26: a band switched off for 30 s produced nothing until it was switched back
on). So the bridge re-registers its SDK session every `BHAPTICS_HTTP_DEVICE_POLL_S` seconds
(default 10) before reading the table; a device switched off shows up at the next tick.
`GET /devices?refresh=1` re-registers immediately. Each re-registration makes the SDK print its
own reconnect lines to the console; set the interval to `0` to stop re-registering, in which
case a disconnection is only seen when something else connects or the bridge restarts.

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

The sparse `motors` form above is the documented one. The bridge expands it to a zero-filled
integer array of the device's motor count before awaiting the SDK. For example, the request above
sets slots 0 and 4 in a 40-element vest array; every other slot is zero. An empty or omitted
`motors` list sends an all-zero array.

**Plain intensity lists are accepted too.** If you already hold a full array, send it as integers
from 0 to 100, one per motor slot:

```json
{"deviceType": 1, "duration": 260, "motors": [40, 40, 40, 40, 40, 40]}
```

A plain list keeps its own length (1 to 40), with no padding or trimming. If you also send
`motorCount`, it must equal that length. A list that mixes plain integers with index/intensity
objects is rejected with a 400. This is the form the first release passed straight through to the
SDK, and the form the [Isotopicity IEM demo](https://isotopicity.com) sends.

**What reaches the Player.** Either way, the bridge calls the SDK's
`play_dot(position, duration_millis, values)` with one integer per motor, 0 to 100. bHaptics'
[Python reference](https://docs.bhaptics.com/sdk/python/references/) states that the list length
must match the device's motor count. The SDK itself does not check the length, so a list of the
wrong length is passed on as it is and the Player decides what to do with it.

**Device layouts:**

| deviceType | Default layout | Default motorCount | Other models |
|-----------|----------------|--------------------|--------------|
| 0 | TactSuit X40 | 40 | TactSuit Pro 32; TactSuit X16 16 (8 front, 8 back) |
| 1 / 2 | Tactosy arms, left / right | 6 | TactSleeve 3 |
| 3 | TactVisor | 4 | |
| 4 / 5 | Tactosy for hands, left / right | 3 | |
| 6 / 7 | Tactosy for feet, left / right | 3 | |
| 8 / 9 | TactGlove, left / right | 8 (DK3) | TactGlove DK2 6 |

Counts for TactSuit Pro, TactSleeve and TactGlove come from bHaptics' documentation
([Python reference](https://docs.bhaptics.com/sdk/python/references/),
[motor guide](https://docs.bhaptics.com/sdk/further/motor)). The TactSuit X16 and Tactosy arm counts
were checked on the author's own hardware.

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

## Configuration

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `BHAPTICS_HTTP_PORT` | `15883` | Port to listen on |
| `BHAPTICS_APP_ID`   | `BHapticsHTTP` | App ID for bHaptics Player auth |
| `BHAPTICS_API_KEY`  | `""` | API key from the bHaptics Developer Portal. Optional: haptics play without one, but the device table is then only sent when a device switches on (see `GET /devices`) |
| `BHAPTICS_HTTP_CORS_ORIGINS` | `""` | Extra origins, comma separated, that may read `GET /health` and `GET /devices` from a web page |
| `BHAPTICS_HTTP_DEVICE_POLL_S` | `10` | Seconds between re-registrations that refresh the device table (how soon a switched-off device is noticed); `0` disables. Skipped without a key, since Player does not answer it |

Web pages served from this machine (`127.0.0.1`, `localhost`, `[::1]`) can always read
`GET /health` and `GET /devices`. The `POST` routes send no CORS headers: a page can still send a
request, but cannot read the reply.

Or copy `examples/tact-config.example.json` to `tact-config.json`, fill it in, and run
`bhaptics-http --config tact-config.json` (a `tact-config.json` in the current folder is also
found without `--config`):

```json
{ "appId": "YOUR_APP_ID", "apiKey": "YOUR_API_KEY" }
```

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

See [`examples/client.js`](../examples/client.js) for a full demo.

## Python library usage

```python
from aiohttp import web
from bhaptics_http.server import make_app, load_config

app_id, api_key = load_config()          # reads env / tact-config.json
app = make_app(app_id, api_key)
web.run_app(app, port=15883)
```

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

**There is no authentication.** Anything that can reach the port can drive your devices. `--host`
defaults to `0.0.0.0` so that WSL2 and other machines can connect; if the client runs on the same
machine, use `--host 127.0.0.1`, and add a firewall rule before opening the port to others.

## Citation

If this software supports your research or creative work, please cite it via Zenodo. This is a
citation request, not a condition of the licence.

> Vollmer, S.C. [MissCrispenCakes]. (2026). *bhaptics-http: HTTP REST bridge for bHaptics haptic hardware* [Software]. Zenodo. https://doi.org/10.5281/zenodo.19025338

That DOI is the concept DOI, which always resolves to the latest release. To cite one exact
release, use that release's own DOI from the "All versions" list on the
[Zenodo record](https://doi.org/10.5281/zenodo.19025338) and add its version number.

## Related

- [tact-python](https://github.com/bhaptics/tact-python) — official Python SDK (this package wraps it)
- [bHaptics Developer Portal](https://developer.bhaptics.com/)
- [VR Ecology Project](https://ieeevr2026.headsetparties.com) — research context where this was first developed
