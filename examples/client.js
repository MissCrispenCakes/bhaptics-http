/**
 * bhaptics-http — Node.js client example
 *
 * Demonstrates how to drive bHaptics hardware from Node.js (or any
 * environment with fetch) once bhaptics-http is running on Windows.
 *
 * Setup:
 *   1. On Windows: pip install bhaptics-http && python -m bhaptics_http
 *   2. On WSL2/Linux/Mac: set HOST to your Windows IP (see WSL2 note below)
 *   3. node examples/client.js
 *
 * WSL2 note:
 *   Your Windows host IP is typically 172.x.x.1 — find it with:
 *     cat /etc/resolv.conf | grep nameserver
 *   Set HOST below (or export BHAPTICS_HTTP_HOST=172.x.x.1 in your shell).
 */

const HOST = process.env.BHAPTICS_HTTP_HOST ?? "localhost";
const PORT = process.env.BHAPTICS_HTTP_PORT ?? "15883";
const BASE = `http://${HOST}:${PORT}`;

// ── helpers ──────────────────────────────────────────────────────────────────

async function health() {
  const res = await fetch(`${BASE}/health`);
  return res.json();
}

/**
 * Play a named Studio event (must be registered in bHaptics Studio first).
 * @param {string} event        Pattern name as registered in Studio
 * @param {number} deviceIndex  0 = first device of this type
 */
async function playEvent(event, deviceIndex = 0) {
  const res = await fetch(`${BASE}/haptic`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event, deviceIndex }),
  });
  return res.json();
}

/**
 * Play raw motor intensities — no Studio pre-registration required.
 *
 * @param {number} deviceType   Device type ID (see device table below)
 * @param {number} duration     Duration in milliseconds
 * @param {Array}  motors       Array of {index, intensity} objects
 *
 * Device types:
 *   0  = TactSuit X16/X40 (chest vest), 16 or 40 motors
 *   1  = Tactosy2 left arm,  6 motors
 *   2  = Tactosy2 right arm, 6 motors
 *   3  = TactVisor head,     4 motors
 *   6  = Tactosy feet left,  3 motors
 *   7  = Tactosy feet right, 3 motors
 *   8  = TactGlove left,     6 motors
 *   9  = TactGlove right,    6 motors
 */
async function playDot(deviceType, duration, motors) {
  const res = await fetch(`${BASE}/haptic/dot`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ deviceType, duration, motors }),
  });
  return res.json();
}

/** Stop all playing patterns. */
async function stopAll() {
  const res = await fetch(`${BASE}/haptic/stop`, { method: "POST" });
  return res.json();
}

// ── demo ─────────────────────────────────────────────────────────────────────

async function demo() {
  console.log("1. Health check …");
  const h = await health();
  console.log("   ", h);

  if (!h.ok) {
    console.error("bHaptics Player not connected. Is it running on Windows?");
    process.exit(1);
  }

  // -- Raw dot pattern: vest upper-front row (motors 0-3), full intensity
  console.log("2. Vest upper-front row — 300 ms …");
  await playDot(0, 300, [
    { index: 0, intensity: 100 },
    { index: 1, intensity: 100 },
    { index: 2, intensity: 100 },
    { index: 3, intensity: 100 },
  ]);
  await new Promise((r) => setTimeout(r, 500));

  // -- Raw dot pattern: left arm full, then right arm full
  console.log("3. Left arm — 200 ms …");
  await playDot(1, 200, [
    { index: 0, intensity: 80 },
    { index: 1, intensity: 80 },
    { index: 2, intensity: 80 },
  ]);
  await new Promise((r) => setTimeout(r, 300));

  console.log("4. Right arm — 200 ms …");
  await playDot(2, 200, [
    { index: 0, intensity: 80 },
    { index: 1, intensity: 80 },
    { index: 2, intensity: 80 },
  ]);
  await new Promise((r) => setTimeout(r, 300));

  // -- Named Studio event (uncomment if you have a pattern registered)
  // console.log("5. Named event …");
  // await playEvent("HeartBeat");

  console.log("5. Stop all.");
  await stopAll();
  console.log("Done.");
}

demo().catch(console.error);
