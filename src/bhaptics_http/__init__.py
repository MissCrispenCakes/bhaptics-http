# bhaptics-http — HTTP REST bridge for bHaptics haptic hardware
# Made by S.C. Vollmer · @MissCrispenCakes
# SPDX-FileCopyrightText: 2026 S.C. Vollmer
# SPDX-License-Identifier: MIT
# See LICENSE for permissions and terms; CITATION.cff for citation metadata.

"""
bhaptics-http
~~~~~~~~~~~~~

HTTP REST bridge for the bHaptics haptic hardware ecosystem.

Wraps the bhaptics-python SDK in a lightweight aiohttp server so that
any language or environment (Node.js, browser fetch, WSL2, Docker, …)
can drive bHaptics vests, arm bands, gloves, and other devices over
plain HTTP — including raw per-motor dot patterns that require no
bHaptics Studio pre-registration.

Quick start:
    pip install bhaptics-http
    python -m bhaptics_http          # starts on port 15883

    # then from anywhere:
    curl http://localhost:15883/health
    curl -X POST http://localhost:15883/haptic/dot \\
         -H 'Content-Type: application/json' \\
         -d '{"deviceType":0,"duration":200,"motors":[{"index":0,"intensity":100}]}'
"""

__version__ = "0.2.0"
__author__  = "S.C. Vollmer"
__license__ = "MIT"

from bhaptics_http.server import make_app, load_config, init_bhaptics

__all__ = ["make_app", "load_config", "init_bhaptics", "__version__"]
