# Changelog

## 0.2.0 (not yet released)

### Fixed
- The bridge keeps one session with Player. Starting it before Player, or restarting Player,
  used to leave several connections open; Player dropped every command on all but one, so
  nothing buzzed. The bridge now waits while Player is not running and registers once when it
  appears.

### Added
- `GET /devices` reports which devices the Player sees as connected, so a client can offer only
  the hardware that is actually worn. It reads Player's device table live on every call, with
  name, role, status and battery per device, and says `pending` until Player has sent the table.
- The bridge watches the device table and logs every connection and disconnection. Player does
  not push disconnections, so with a key the watcher re-registers every
  `BHAPTICS_HTTP_DEVICE_POLL_S` seconds (default 10) to fetch a fresh table;
  `GET /devices?refresh=1` forces one. Without a key Player does not answer, so it is skipped.
- `GET /health` and `GET /devices` can be read by web pages served from the same machine. The
  `POST` routes are unchanged. `BHAPTICS_HTTP_CORS_ORIGINS` adds further origins.

## 0.1.1 (never published; included in 0.2.0)

### Fixed
- Every SDK call is now awaited. `bhaptics-python` 0.2.x returns an awaitable from each call, so
  before this the connection check always looked true and the reconnect logic never ran properly.
- `retry_initialize` is called with the app id and API key the SDK requires.
- The third `registry_and_initialize` argument is an empty string, as bHaptics' documentation
  instructs, and no longer an application name.
- Sparse `motors` entries (`{"index", "intensity"}`) are expanded to a full integer array before
  they reach the SDK, which accepts only integers. Invalid requests return 400 without touching
  the SDK.
- `bhaptics-python` 0.2.2 or newer is required. With 0.2.0, `is_connected()` never turns true, so
  once every call was awaited, the bridge refused every haptic request with 503.

### Added
- The README leads with the `bhaptics-http` command, so one name installs, upgrades and runs it,
  and explains why `python -m` needs the underscore (`bhaptics_http`).
- `motors` may also be a plain list of integer intensities (0-100), one per motor, which keeps its
  own length. This is the form the first release passed straight through, and the form the
  Isotopicity IEM demo sends.
- Documentation of the request format, device layouts, SDK and Python compatibility, and the
  absence of authentication.
- `CITATION.cff` and `.zenodo.json`, so the GitHub, PyPI and Zenodo pages describe the project in
  the same way.

### Changed
- The Citation section carries the real Zenodo concept DOI, with the author name and handle,
  in place of a placeholder.

## 0.1.0 - 2026-03-14

- First release.
