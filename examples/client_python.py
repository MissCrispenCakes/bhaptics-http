"""
bhaptics-http — Python client example (requests)

Shows how to call the server from a separate Python script
(e.g. from WSL2 or another machine).

Setup:
    pip install requests
    # On Windows: python -m bhaptics_http
    # Then: python examples/client_python.py
"""

import os
import time
import requests

HOST = os.environ.get("BHAPTICS_HTTP_HOST", "localhost")
PORT = os.environ.get("BHAPTICS_HTTP_PORT", "15883")
BASE = f"http://{HOST}:{PORT}"


def health():
    return requests.get(f"{BASE}/health").json()


def play_dot(device_type: int, duration: int, motors: list[dict]) -> dict:
    return requests.post(
        f"{BASE}/haptic/dot",
        json={"deviceType": device_type, "duration": duration, "motors": motors},
    ).json()


def play_event(event: str, device_index: int = 0) -> dict:
    return requests.post(
        f"{BASE}/haptic",
        json={"event": event, "deviceIndex": device_index},
    ).json()


def stop_all() -> dict:
    return requests.post(f"{BASE}/haptic/stop").json()


if __name__ == "__main__":
    print("Health:", health())

    print("Left arm sweep …")
    for i in range(6):
        play_dot(1, 80, [{"index": i, "intensity": 100}])
        time.sleep(0.1)

    print("Right arm sweep …")
    for i in range(6):
        play_dot(2, 80, [{"index": i, "intensity": 100}])
        time.sleep(0.1)

    print("Stop all.")
    stop_all()
