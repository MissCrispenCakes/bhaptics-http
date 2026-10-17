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
            'is_connected': True, 'is_bhaptics_player_running': False,
            'play_dot': 42, 'play_event': 42, 'stop_all': None, 'close': None,
        }.items()
    })
    monkeypatch.setitem(sys.modules, 'bhaptics_python', sdk)
    # Also replace the reference if another test has already imported the bridge.
    server = importlib.import_module('bhaptics_http.server')
    monkeypatch.setattr(server, 'bh', sdk)
    return sdk


@pytest_asyncio.fixture
async def client(sdk):
    from bhaptics_http.server import make_app
    async with TestClient(TestServer(make_app('test-app', 'test-key'))) as client:
        yield client
    sdk.close.assert_awaited_once_with()


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
    *({'motors': v} for v in [None, {}, 'bad', [1], [[]], [{}], [{'index': 0}], [{'intensity': 1}]]),
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
    sdk.retry_initialize.assert_awaited_once_with('test-app', 'test-key')
    assert sdk.registry_and_initialize.await_count == 2
    sdk.play_dot.assert_not_called()
    sdk.play_event.assert_not_called()


@pytest.mark.asyncio
async def test_reconnect_success(client, sdk):
    sdk.is_connected.side_effect = [False, True]
    response = await client.post('/haptic/dot', json={})
    assert response.status == 200
    sdk.retry_initialize.assert_awaited_once_with('test-app', 'test-key')
    sdk.play_dot.assert_awaited_once()


@pytest.mark.asyncio
async def test_background_reconnect_and_cleanup(sdk, monkeypatch):
    from bhaptics_http import server
    monkeypatch.setattr(server, 'RECONNECT_INTERVAL', 0.001)
    sdk.is_connected.return_value = False
    retried = asyncio.Event()
    async def retry(*args):
        retried.set()
    sdk.retry_initialize.side_effect = retry
    async with TestClient(TestServer(server.make_app('test-app', 'test-key'))):
        await asyncio.wait_for(retried.wait(), 1)
    sdk.retry_initialize.assert_awaited_with('test-app', 'test-key')
    sdk.close.assert_awaited_once_with()
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
