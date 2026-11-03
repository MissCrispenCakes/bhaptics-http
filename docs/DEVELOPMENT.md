# bhaptics-http development

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
The minimum dependency is 0.2.2: on hardware, 0.2.0 never reported a connection.

All SDK operations run on the application's event loop. Library callers using
`init_bhaptics` directly must now use `await init_bhaptics(app_id, api_key)`.
`make_app()` remains synchronous; it awaits initialization on startup, cancels
its reconnect task on cleanup, and awaits SDK `close()`.

The tests use no hardware. On hardware (Windows, bHaptics Player 3.6.9, TactSuit X16 and
Tactosy2 arm bands, November 2026), with and without a key: `/health` and `/devices`, a single
buzz, every arm and vest slot, a device switched off and on, the bridge started before Player,
and Player restarted. Not yet checked on hardware: `/haptic` event playback and `/haptic/stop`.
