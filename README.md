# bhaptics-http
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19025338.svg)](https://doi.org/10.5281/zenodo.19025338)

HTTP bridge for bHaptics hardware. Run it next to bHaptics Player on Windows, then drive vests,
arm bands, gloves and other devices from any language, WSL2 or another machine.

The main feature is `/haptic/dot`: raw per-motor intensities, with no bHaptics Studio setup.

## Install

```bash
pip install bhaptics-http
```

Windows only, on the same machine as bHaptics Player. Python 3.9 or newer.

## Quick start

Start bHaptics Player, then:

```bash
bhaptics-http
```

If that command isn't found, use `python -m bhaptics_http` (underscore).

Test it:

```bash
curl http://localhost:15883/health

curl -X POST http://localhost:15883/haptic/dot \
  -H 'Content-Type: application/json' \
  -d '{"deviceType":0,"duration":300,"motors":[{"index":0,"intensity":100}]}'
```

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Is the bridge connected to Player |
| GET | `/devices` | Which devices are connected |
| POST | `/haptic` | Play a pattern registered in bHaptics Studio |
| POST | `/haptic/dot` | Play raw per-motor intensities |
| POST | `/haptic/stop` | Stop everything |

Request and response formats, device layouts, WSL2 setup and more:
[docs/API.md](https://github.com/MissCrispenCakes/bhaptics-http/blob/basecamp/docs/API.md).

## Configuration

| Environment variable | Default | |
|---|---|---|
| `BHAPTICS_HTTP_PORT` | `15883` | Port |
| `BHAPTICS_APP_ID` | `BHapticsHTTP` | App ID |
| `BHAPTICS_API_KEY` | `""` | API key (optional; see below) |
| `BHAPTICS_HTTP_CORS_ORIGINS` | `""` | Extra web origins that may read `/health` and `/devices` |
| `BHAPTICS_HTTP_DEVICE_POLL_S` | `10` | Seconds between device-list refreshes (with a key); `0` turns it off |

Haptics play without a key. To use your own app ID and API key from the bHaptics Developer
Portal, copy `examples/tact-config.example.json`, fill it in, and run
`bhaptics-http --config tact-config.json`. With a key, `/devices` is live; without one, Player
lists a device only when it switches on while the bridge is running, never reports a
switch-off, and devices already on when the bridge starts show as `pending`.

There is no authentication: anything that can reach the port can drive your devices. To keep it
to this machine, run `bhaptics-http --host 127.0.0.1`.

Used by [VR Ecology Project](https://ieeevr2026.headsetparties.com), [Vivarium](https://vivarium.headsetparties.com), [Alice Lab](https://alicelab.world/) ([worldmaking](https://github.com/worldmaking)) and [Isotopicity](https://isotopicity.com).

## Citation

> Vollmer, S.C. [MissCrispenCakes]. (2026). *bhaptics-http: HTTP REST bridge for bHaptics haptic hardware* [Software]. Zenodo. https://doi.org/10.5281/zenodo.19025338

## Contributing

Bug reports and questions: [Issues](https://github.com/MissCrispenCakes/bhaptics-http/issues).

## License

MIT © S.C. Vollmer (@MissCrispenCakes).
[Changelog](https://github.com/MissCrispenCakes/bhaptics-http/blob/basecamp/CHANGELOG.md)
