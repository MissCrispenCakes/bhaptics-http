"""HTTP regression tests; no SDK installation, Player, or hardware required."""
import asyncio
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture
def sdk(monkeypatch):
    sdk = SimpleNamespace(**{
        name: AsyncMock(return_value=value)
        for name, value in {
            'registry_and_initialize': True, 'retry_initialize': True,
            'is_connected': True, 'is_bhaptics_player_running': True,
            'play_dot': 42, 'play_event': 42, 'stop_all': None, 'close': None,
        }.items()
    })
    monkeypatch.setitem(sys.modules, 'bhaptics_python', sdk)
    # Also replace the reference if another test has already imported the bridge.
    server = importlib.import_module('bhaptics_http.server')
    monkeypatch.setattr(server, 'bh', sdk)
    # The device table is module state; start every test with "no list received yet".
    monkeypatch.setattr(server, '_devices', {'received': False, 'list': [], 'connected': None})
    monkeypatch.setattr(server, '_last_reregister', float('-inf'))
    monkeypatch.setattr(server, '_needs_fresh_session', False)
    return sdk


@pytest_asyncio.fixture
async def client(sdk):
    from bhaptics_http.server import make_app
    async with TestClient(TestServer(make_app('test-app', 'test-key'))) as client:
        yield client
    sdk.close.assert_awaited_with()      # on shutdown (reconnects also close first)


@pytest.mark.asyncio
async def test_sparse_dot(client, sdk):
    response = await client.post('/haptic/dot', json={
        'duration': 300, 'motors': [
            {'index': 39, 'intensity': 100}, {'index': 4, 'intensity': 60},
            {'index': 0, 'intensity': 0},
        ],
    })
    assert response.status == 200
    assert await response.json() == {'ok': True}
    expected = [0] * 40
    expected[39], expected[4] = 100, 60
    sdk.play_dot.assert_awaited_once_with(0, 300, expected)
    sdk.registry_and_initialize.assert_awaited_once_with('test-app', 'test-key', '')


@pytest.mark.asyncio
@pytest.mark.parametrize('position,count', [(0,40),(1,6),(2,6),(3,4),(4,3),(5,3),(6,3),(7,3),(8,8),(9,8)])
async def test_default_layouts(client, sdk, position, count):
    response = await client.post('/haptic/dot', json={
        'deviceType': position, 'motors': [{'index': count - 1, 'intensity': 1}],
    })
    assert response.status == 200
    sdk.play_dot.assert_awaited_once_with(position, 100, [0] * (count - 1) + [1])


@pytest.mark.asyncio
async def test_layout_override(client, sdk):
    response = await client.post('/haptic/dot', json={'motorCount': 32, 'motors': [{'index': 31, 'intensity': 50}]})
    assert response.status == 200
    sdk.play_dot.assert_awaited_once_with(0, 100, [0] * 31 + [50])


@pytest.mark.asyncio
async def test_empty_motors(client, sdk):
    response = await client.post('/haptic/dot', json={})
    assert response.status == 200
    sdk.play_dot.assert_awaited_once_with(0, 100, [0] * 40)


BAD_BODIES = [None, [], 'bad', 1,
    *({'duration': v} for v in [0, -1, 2**31, 1.5, '100', True, None]),
    *({'deviceType': v} for v in [-1, 10, 0.5, '0', False, None]),
    *({'motorCount': v} for v in [0, 41, '40', True, None]),
    *({'motors': v} for v in [None, {}, 'bad', ['1'], [[]], [{}], [{'index': 0}], [{'intensity': 1}]]),
    *({'motors': [{'index': v, 'intensity': 50}]} for v in [-1,40,1.5,'0',True,None]),
    *({'motors': [{'index': 0, 'intensity': v}]} for v in [-1,101,1.5,'50',False,None]),
    {'motors': [{'index': 0, 'intensity': 1}, {'index': 0, 'intensity': 2}]},
    {'deviceType': 8, 'motors': [{'index': 8, 'intensity': 50}]},
    {'motorCount': 32, 'motors': [{'index': 32, 'intensity': 50}]},
]


@pytest.mark.asyncio
@pytest.mark.parametrize('body', BAD_BODIES)
async def test_invalid_dot_never_calls_sdk(client, sdk, body):
    sdk.is_connected.reset_mock()
    # data avoids aiohttp treating json=None as an absent body.
    import json
    response = await client.post('/haptic/dot', data=json.dumps(body), headers={'Content-Type': 'application/json'})
    assert response.status == 400
    assert 'error' in await response.json()
    sdk.play_dot.assert_not_called()
    sdk.is_connected.assert_not_called()
    sdk.retry_initialize.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_json(client, sdk):
    response = await client.post('/haptic/dot', data='{')
    assert response.status == 400
    sdk.play_dot.assert_not_called()


@pytest.mark.asyncio
async def test_future_status_values(client, sdk):
    # Native PyO3 functions return Futures, rather than Python coroutines.
    def future(value):
        result = asyncio.get_running_loop().create_future()
        result.set_result(value)
        return result
    sdk.is_connected = lambda: future(False)
    sdk.is_bhaptics_player_running = lambda: future(False)
    response = await client.get('/health')
    assert await response.json() == {'ok': False, 'backend': 'bhaptics-python', 'player': False}


@pytest.mark.asyncio
async def test_other_endpoints(client, sdk):
    response = await client.post('/haptic', json={'event': 'Heartbeat', 'deviceIndex': 2})
    assert response.status == 200
    sdk.play_event.assert_awaited_once_with('Heartbeat', 2)
    response = await client.post('/haptic/stop')
    assert response.status == 200
    sdk.stop_all.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize('path,body,method', [('/haptic/dot', {}, 'play_dot'),('/haptic', {'event':'Heartbeat'}, 'play_event'),('/haptic/stop', {}, 'stop_all')])
async def test_awaited_sdk_errors(client, sdk, path, body, method):
    getattr(sdk, method).side_effect = RuntimeError('SDK failure')
    response = await client.post(path, json=body)
    assert response.status == 500
    assert await response.json() == {'error': 'SDK failure'}
    getattr(sdk, method).assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('path,body,method', [('/haptic/dot', {}, 'play_dot'),('/haptic', {'event':'Heartbeat'}, 'play_event')])
async def test_sdk_rejection(client, sdk, path, body, method):
    getattr(sdk, method).return_value = -1
    response = await client.post(path, json=body)
    assert response.status == 500


@pytest.mark.asyncio
@pytest.mark.parametrize('path,body', [('/haptic/dot', {}),('/haptic', {'event':'Heartbeat'})])
async def test_disconnected(client, sdk, path, body):
    sdk.is_connected.return_value = False
    response = await client.post(path, json=body)
    assert response.status == 503
    sdk.retry_initialize.assert_not_called()            # a reconnect is a fresh session, not a re-init
    assert sdk.registry_and_initialize.await_count == 2  # startup + one reconnect attempt
    sdk.close.assert_awaited()                           # old session closed before registering again
    sdk.play_dot.assert_not_called()
    sdk.play_event.assert_not_called()


@pytest.mark.asyncio
async def test_reconnect_success(client, sdk):
    sdk.is_connected.side_effect = [False, False, True]  # handler check, under-lock check, after registering
    response = await client.post('/haptic/dot', json={})
    assert response.status == 200
    assert sdk.registry_and_initialize.await_args_list[-1].args == ('test-app', 'test-key', '')
    sdk.play_dot.assert_awaited_once()


@pytest.mark.asyncio
async def test_background_reconnect_and_cleanup(sdk, monkeypatch):
    from bhaptics_http import server
    monkeypatch.setattr(server, 'RECONNECT_INTERVAL', 0.001)
    sdk.is_connected.return_value = False
    retried = asyncio.Event()
    async def register(*args):
        if sdk.registry_and_initialize.await_count >= 2:   # the first is startup
            retried.set()
        return True
    sdk.registry_and_initialize.side_effect = register
    async with TestClient(TestServer(server.make_app('test-app', 'test-key'))):
        await asyncio.wait_for(retried.wait(), 1)
    sdk.registry_and_initialize.assert_awaited_with('test-app', 'test-key', '')
    sdk.close.assert_awaited_with()
    assert not any(task.get_coro().__name__ == 'reconnect_loop' for task in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_pending_native_future_is_awaited(client, sdk):
    from unittest.mock import Mock
    result = asyncio.get_running_loop().create_future()
    called = asyncio.Event()
    def play(*args):
        called.set()
        return result
    sdk.play_dot = Mock(side_effect=play)
    response_task = asyncio.create_task(client.post('/haptic/dot', json={}))
    try:
        await asyncio.wait_for(called.wait(), 1)
        assert not response_task.done()
        result.set_result(42)
        response = await asyncio.wait_for(response_task, 1)
        assert response.status == 200
    finally:
        if not response_task.done():
            response_task.cancel()
        await asyncio.gather(response_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_health_errors(client, sdk):
    sdk.is_connected.side_effect = RuntimeError('not ready')
    sdk.is_bhaptics_player_running.side_effect = RuntimeError('not available')
    response = await client.get('/health')
    assert await response.json() == {'ok': False, 'backend': 'bhaptics-python', 'player': None}


# ── Plain intensity lists ────────────────────────────────────────────────────
# Clients that already hold a full per-motor array (the IEM browser demo is one)
# send it as a list of integers. That must keep working next to the sparse form.

import json as _json


async def _post_like_the_demo(client, payload):
    # The demo posts with mode 'no-cors', which forces a text/plain content type.
    return await client.post(
        '/haptic/dot', data=_json.dumps(payload), headers={'Content-Type': 'text/plain'})


@pytest.mark.asyncio
async def test_demo_probe_request(client, sdk):
    response = await _post_like_the_demo(client, {'deviceType': 0, 'duration': 20, 'motors': [0] * 16})
    assert response.status == 200
    sdk.play_dot.assert_awaited_once_with(0, 20, [0] * 16)


@pytest.mark.asyncio
@pytest.mark.parametrize('position,count', [(0, 16), (1, 6), (2, 6), (6, 3), (7, 3)])
async def test_demo_device_requests(client, sdk, position, count):
    response = await _post_like_the_demo(
        client, {'deviceType': position, 'duration': 260, 'motors': [31] * count})
    assert response.status == 200
    sdk.play_dot.assert_awaited_once_with(position, 260, [31] * count)


@pytest.mark.asyncio
async def test_plain_list_keeps_its_own_length(client, sdk):
    response = await client.post('/haptic/dot', json={'motors': [5, 0, 100]})
    assert response.status == 200
    sdk.play_dot.assert_awaited_once_with(0, 100, [5, 0, 100])


@pytest.mark.asyncio
async def test_plain_list_may_state_a_matching_motor_count(client, sdk):
    response = await client.post('/haptic/dot', json={'motorCount': 3, 'motors': [1, 2, 3]})
    assert response.status == 200
    sdk.play_dot.assert_awaited_once_with(0, 100, [1, 2, 3])


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [
    {'motorCount': 4, 'motors': [1, 2, 3]},                       # count disagrees with the list
    {'motors': [1, 2, 101]},                                      # above 100
    {'motors': [1, -1, 3]},                                       # below 0
    {'motors': [1, 2.5, 3]},                                      # not an integer
    {'motors': [1, True, 3]},                                     # bool is not an intensity
    {'motors': [1, '2', 3]},                                      # a string
    {'motors': [0] * 41},                                         # longer than any device
    {'motors': [1, {'index': 2, 'intensity': 50}]},               # plain and sparse mixed
    {'motors': [{'index': 2, 'intensity': 50}, 7]},               # sparse and plain mixed
])
async def test_plain_list_validation(client, sdk, body):
    response = await client.post('/haptic/dot', json=body)
    assert response.status == 400
    assert 'error' in await response.json()
    sdk.play_dot.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_list_is_still_an_all_zero_default_array(client, sdk):
    response = await client.post('/haptic/dot', json={'motors': []})
    assert response.status == 200
    sdk.play_dot.assert_awaited_once_with(0, 100, [0] * 40)


# ── Device presence and read-only CORS ───────────────────────────────────────

@pytest.mark.asyncio
async def test_devices_reports_connected_positions(client, sdk):
    present = {1, 2}
    sdk.is_bhaptics_device_connected = AsyncMock(side_effect=lambda position: position in present)
    response = await client.get('/devices')
    assert response.status == 200
    body = await response.json()
    assert body['ok'] is True
    assert body['present'] == [1, 2]
    assert body['devices'] == {str(p): p in present for p in range(10)}
    assert sdk.is_bhaptics_device_connected.await_count == 10


@pytest.mark.asyncio
async def test_devices_when_nothing_is_connected(client, sdk):
    sdk.is_bhaptics_device_connected = AsyncMock(return_value=False)
    body = await (await client.get('/devices')).json()
    assert body['present'] == []
    assert set(body['devices'].values()) == {False}


@pytest.mark.asyncio
async def test_devices_treats_an_sdk_error_as_not_connected(client, sdk):
    async def flaky(position):
        if position == 2:
            raise RuntimeError('boom')
        return position == 1
    sdk.is_bhaptics_device_connected = flaky
    body = await (await client.get('/devices')).json()
    assert body['present'] == [1]


_PLAYER_LIST = (
    '[{"deviceName":"Tactosy2_V3 (L)","position":1,"connected":true,"connectionStatus":"Connected","battery":98},'
    ' {"deviceName":"TactSuitX16","position":0,"connected":false,"connectionStatus":"Disconnected","battery":100},'
    ' {"deviceName":"Tactosy2_V3 (R)","position":2,"connected":true,"connectionStatus":"Connected","battery":99}]'
)


@pytest.mark.asyncio
async def test_devices_reads_the_player_table_live(client, sdk):
    sdk.get_device_info_json = AsyncMock(return_value=_PLAYER_LIST)
    sdk.is_bhaptics_device_connected = AsyncMock(return_value=False)   # stale flags must be ignored
    body = await (await client.get('/devices')).json()
    assert body['source'] == 'player' and body['pending'] is False
    assert body['present'] == [1, 2]
    assert body['devices']['0'] is False and body['devices']['1'] is True
    assert [d['position'] for d in body['list']] == [0, 1, 2]
    assert body['list'][0] == {'position': 0, 'name': 'TactSuitX16', 'role': 'vest', 'connected': False,
                               'status': 'Disconnected', 'battery': 100}
    assert sdk.is_bhaptics_device_connected.await_count == 0


@pytest.mark.asyncio
async def test_devices_is_pending_until_player_sends_the_table(client, sdk):
    sdk.get_device_info_json = AsyncMock(return_value='')
    sdk.is_bhaptics_device_connected = AsyncMock(side_effect=lambda position: position == 0)
    body = await (await client.get('/devices')).json()
    assert body['pending'] is True and body['source'] == 'flags'
    assert body['present'] == [0]


@pytest.mark.asyncio
async def test_device_changes_are_logged(sdk, caplog):
    from bhaptics_http import server
    server._devices.update({'received': False, 'list': [], 'connected': None})
    sdk.get_device_info_json = AsyncMock(return_value=_PLAYER_LIST)
    with caplog.at_level('INFO', logger='bhaptics_http'):
        await server.refresh_devices()
        assert 'list received from Player' in caplog.text
        assert 'left arm (Tactosy2_V3 (L), 98 %)' in caplog.text
        caplog.clear()
        sdk.get_device_info_json = AsyncMock(return_value=_PLAYER_LIST.replace(
            '"position":0,"connected":false', '"position":0,"connected":true').replace(
            '"position":2,"connected":true', '"position":2,"connected":false'))
        await server.refresh_devices()
    assert '[devices] vest (TactSuitX16, 100 %) connected' in caplog.text
    assert '[devices] right arm (Tactosy2_V3 (R), 99 %) disconnected' in caplog.text
    assert server._devices['connected'] == {0, 1}


@pytest.mark.asyncio
async def test_reconnect_loop_watches_devices(sdk, monkeypatch, caplog):
    from bhaptics_http import server
    server._devices.update({'received': False, 'list': [], 'connected': None})
    monkeypatch.setattr(server, 'RECONNECT_INTERVAL', 0.001)
    sdk.get_device_info_json = AsyncMock(return_value=_PLAYER_LIST)
    task = asyncio.create_task(server.reconnect_loop())
    with caplog.at_level('INFO', logger='bhaptics_http'):
        for _ in range(300):                       # up to ~3 s: a tick may re-register first (0.3 s settle)
            await asyncio.sleep(0.01)
            if server._devices['received']:
                break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert server._devices['connected'] == {1, 2}
    assert 'list received from Player' in caplog.text


@pytest.mark.asyncio
async def test_devices_reregisters_to_see_disconnects(client, sdk, monkeypatch):
    from bhaptics_http import server
    monkeypatch.setattr(server, '_app_id', 'app'); monkeypatch.setattr(server, '_api_key', 'key')
    monkeypatch.setattr(server, 'DEVICE_POLL_S', 1000.0)
    sdk.get_device_info_json = AsyncMock(return_value=_PLAYER_LIST)
    sdk.retry_initialize = AsyncMock(return_value=None)
    await client.get('/devices')                      # first call re-registers (interval elapsed since 0)
    await client.get('/devices')                      # within the interval: no re-registration
    assert sdk.retry_initialize.await_count == 1
    assert sdk.retry_initialize.await_args.args == ('app', 'key')
    await client.get('/devices?refresh=1')            # forced
    assert sdk.retry_initialize.await_count == 2


@pytest.mark.asyncio
async def test_device_polling_can_be_disabled(client, sdk, monkeypatch):
    from bhaptics_http import server
    monkeypatch.setattr(server, '_app_id', 'app'); monkeypatch.setattr(server, '_api_key', '')
    monkeypatch.setattr(server, 'DEVICE_POLL_S', 0.0)
    sdk.get_device_info_json = AsyncMock(return_value=_PLAYER_LIST)
    sdk.retry_initialize = AsyncMock(return_value=None)
    await client.get('/devices')
    assert sdk.retry_initialize.await_count == 0
    await client.get('/devices?refresh=1')
    assert sdk.retry_initialize.await_count == 1


@pytest.mark.asyncio
async def test_devices_when_the_sdk_cannot_report_presence(client, sdk):
    if hasattr(sdk, 'is_bhaptics_device_connected'):
        del sdk.is_bhaptics_device_connected
    body = await (await client.get('/devices')).json()
    assert body['devices'] is None and body['present'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('origin', ['http://127.0.0.1:8080', 'http://localhost:5173', 'https://localhost', 'http://[::1]:9000'])
@pytest.mark.parametrize('path', ['/health', '/devices'])
async def test_local_pages_can_read_the_read_only_routes(client, sdk, origin, path):
    sdk.is_bhaptics_device_connected = AsyncMock(return_value=False)
    response = await client.get(path, headers={'Origin': origin})
    assert response.headers['Access-Control-Allow-Origin'] == origin
    assert 'Origin' in response.headers['Vary']


@pytest.mark.asyncio
@pytest.mark.parametrize('origin', ['https://example.com', 'http://127.0.0.1.evil.com', 'http://localhost.evil.com:8080', 'null', 'file://'])
async def test_other_origins_get_no_cors_header(client, sdk, origin):
    sdk.is_bhaptics_device_connected = AsyncMock(return_value=False)
    response = await client.get('/devices', headers={'Origin': origin})
    assert 'Access-Control-Allow-Origin' not in response.headers


@pytest.mark.asyncio
async def test_extra_origins_can_be_allowed(client, sdk, monkeypatch):
    monkeypatch.setenv('BHAPTICS_HTTP_CORS_ORIGINS', 'https://lab.example, https://other.example')
    sdk.is_bhaptics_device_connected = AsyncMock(return_value=False)
    ok = await client.get('/devices', headers={'Origin': 'https://lab.example'})
    no = await client.get('/devices', headers={'Origin': 'https://nope.example'})
    assert ok.headers['Access-Control-Allow-Origin'] == 'https://lab.example'
    assert 'Access-Control-Allow-Origin' not in no.headers


@pytest.mark.asyncio
async def test_preflight_for_read_only_routes(client, sdk):
    response = await client.options('/devices', headers={
        'Origin': 'http://127.0.0.1:8080', 'Access-Control-Request-Method': 'GET'})
    assert response.status == 204
    assert response.headers['Access-Control-Allow-Origin'] == 'http://127.0.0.1:8080'
    assert response.headers['Access-Control-Allow-Methods'] == 'GET, OPTIONS'
    refused = await client.options('/devices', headers={
        'Origin': 'https://example.com', 'Access-Control-Request-Method': 'GET'})
    assert 'Access-Control-Allow-Origin' not in refused.headers


@pytest.mark.asyncio
async def test_post_routes_never_get_cors_headers(client, sdk):
    response = await client.post('/haptic/dot', json={'motors': [0]}, headers={'Origin': 'http://127.0.0.1:8080'})
    assert response.status == 200
    assert 'Access-Control-Allow-Origin' not in response.headers


# ── One session at a time (2026-11-03: sockets queued while Player was down all connected at
#    once, Player authenticated one, and every buzz on the others was dropped) ──────────────

async def _run_loop_until(server, cond, ticks=300):
    task = asyncio.create_task(server.reconnect_loop())
    for _ in range(ticks):
        await asyncio.sleep(0.005)
        if cond():
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_no_registration_while_player_is_down(sdk, monkeypatch, caplog):
    from bhaptics_http import server
    monkeypatch.setattr(server, 'RECONNECT_INTERVAL', 0.001)
    sdk.is_bhaptics_player_running.return_value = False
    sdk.is_connected.return_value = False
    with caplog.at_level('INFO', logger='bhaptics_http'):
        assert await server.init_bhaptics('app', 'key') is False
        await _run_loop_until(server, lambda: False, ticks=40)
    sdk.registry_and_initialize.assert_not_called()
    sdk.retry_initialize.assert_not_called()
    assert caplog.text.count('Player is not running — waiting') == 1   # said once, not every tick
    assert sdk.close.await_count == 1                                  # SDK's own retries stopped once


@pytest.mark.asyncio
async def test_player_starting_gets_exactly_one_session(sdk, monkeypatch):
    from bhaptics_http import server
    monkeypatch.setattr(server, 'RECONNECT_INTERVAL', 0.001)
    monkeypatch.setattr(server, 'DEVICE_POLL_S', 0.0)
    running = {'v': False}
    sdk.is_bhaptics_player_running.side_effect = lambda: running['v']
    state = {'connected': False}
    sdk.is_connected.side_effect = lambda: state['connected']
    async def register(*args):
        state['connected'] = True
        return True
    sdk.registry_and_initialize.side_effect = register
    await server.init_bhaptics('app', 'key')
    task = asyncio.create_task(server.reconnect_loop())
    await asyncio.sleep(0.03)
    running['v'] = True                                  # Player starts
    for _ in range(200):
        await asyncio.sleep(0.005)
        if sdk.registry_and_initialize.await_count:
            break
    await asyncio.sleep(0.03)                            # more ticks: must not register again
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sdk.registry_and_initialize.await_count == 1
    assert sdk.registry_and_initialize.await_args.args == ('app', 'key', '')


@pytest.mark.asyncio
async def test_player_restart_registers_afresh_even_if_sdk_says_connected(sdk, monkeypatch):
    from bhaptics_http import server
    monkeypatch.setattr(server, 'RECONNECT_INTERVAL', 0.001)
    monkeypatch.setattr(server, 'DEVICE_POLL_S', 0.0)
    monkeypatch.setattr(server, '_app_id', 'app'); monkeypatch.setattr(server, '_api_key', 'key')
    running = {'v': False}                               # Player has just quit
    sdk.is_bhaptics_player_running.side_effect = lambda: running['v']
    sdk.is_connected.return_value = True                 # the SDK's own retry got back in, unauthenticated
    task = asyncio.create_task(server.reconnect_loop())
    await asyncio.sleep(0.03)
    running['v'] = True
    for _ in range(200):
        await asyncio.sleep(0.005)
        if sdk.registry_and_initialize.await_count:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sdk.registry_and_initialize.await_count == 1
    assert server._needs_fresh_session is False


@pytest.mark.asyncio
async def test_no_device_reregistration_without_a_key(client, sdk, monkeypatch):
    from bhaptics_http import server
    monkeypatch.setattr(server, '_app_id', 'BHapticsHTTP'); monkeypatch.setattr(server, '_api_key', '')
    sdk.get_device_info_json = AsyncMock(return_value=_PLAYER_LIST)
    sdk.retry_initialize = AsyncMock(return_value=None)
    await client.get('/devices')
    assert sdk.retry_initialize.await_count == 0         # Player never sends the table to an unregistered id


def test_example_config_unchanged_runs_without_a_key(tmp_path, monkeypatch, caplog):
    import json, shutil
    from pathlib import Path
    from bhaptics_http.server import load_config
    monkeypatch.delenv('BHAPTICS_APP_ID', raising=False); monkeypatch.delenv('BHAPTICS_API_KEY', raising=False)
    example = Path(__file__).parent.parent / 'examples' / 'tact-config.example.json'
    cfg = tmp_path / 'tact-config.json'
    shutil.copy(example, cfg)
    with caplog.at_level('WARNING', logger='bhaptics_http'):
        assert load_config(cfg) == ('BHapticsHTTP', '')
    assert 'placeholder' in caplog.text
    cfg.write_text(json.dumps({'appId': 'real-id', 'apiKey': 'real-key'}))
    assert load_config(cfg) == ('real-id', 'real-key')
