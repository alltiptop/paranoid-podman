"""Opt-in proof of selected-port access using temporary host loopback listeners."""

import contextlib
import json
import os
import subprocess
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

NETWORK_PROBE = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import { chromium } from 'playwright-core';

const [selected, other, enabled] = process.argv.slice(1);
const reachable = (host, port) => new Promise(resolve => {
  const socket = net.createConnection({ host, port: Number(port) });
  const finish = result => { socket.destroy(); resolve(result); };
  socket.setTimeout(1000, () => finish(false));
  socket.on('connect', () => finish(true));
  socket.on('error', () => finish(false));
});
const status = fs.readFileSync('/proc/self/status', 'utf8');
assert.notEqual(process.getuid(), 0);
for (const key of ['CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb']) {
  assert.match(status, new RegExp(`^${key}:\\s+0+$`, 'm'));
}
assert.match(status, /^NoNewPrivs:\s+1$/m);
assert.match(status, /^Seccomp:\s+2$/m);
assert.equal(await reachable('127.0.0.1', selected), enabled === 'yes');
assert.equal(await reachable('127.0.0.1', other), false, 'unselected host port');
assert.equal(await reachable('host.containers.internal', other), false, 'host alias');
const interfaces = Object.values(os.networkInterfaces()).flat()
  .filter(iface => iface && iface.family === 'IPv4' && !iface.internal);
assert.ok(interfaces.length > 0);
for (const iface of interfaces) {
  assert.equal(await reachable(iface.address, selected), false, 'loopback-only bind');
}
let pngBytes = 0;
if (enabled === 'yes') {
  const browser = await chromium.launch({ headless: true, chromiumSandbox: true, timeout: 20000 });
  try {
    const page = await browser.newPage();
    const response = await page.goto(`http://127.0.0.1:${selected}/probe`, { timeout: 15000 });
    assert.equal(response.status(), 200);
    assert.equal(await page.locator('body').innerText(), 'selected loopback service');
    const png = await page.screenshot();
    assert.equal(png.subarray(1, 4).toString(), 'PNG');
    pngBytes = png.length;
  } finally { await browser.close(); }
}
console.log(JSON.stringify({ selected: Number(selected), other: Number(other),
  enabled: enabled === 'yes', unselectedBlocked: true, loopbackOnly: true,
  uid: process.getuid(), capabilitiesZero: true, noNewPrivileges: true, seccomp: true, pngBytes }));
"""


class ProbeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"selected loopback service"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def loopback_service():
    with ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join(timeout=5)


@unittest.skipUnless(
    os.environ.get("PARANOID_PODMAN_RUN_LOOPBACK_TESTS") == "1",
    "set PARANOID_PODMAN_RUN_LOOPBACK_TESTS=1 for temporary host-listener tests",
)
class LoopbackContainerTests(unittest.TestCase):
    def test_only_explicit_port_is_reachable_and_browser_sandbox_stays_enabled(self):
        environment = {
            **os.environ,
            "PODMAN_GUARD_INSTALLATION_ID": uuid.uuid4().hex + uuid.uuid4().hex,
        }
        with loopback_service() as selected, loopback_service() as other:
            for network, enabled in (
                ("pasta", "no"),
                (f"pasta:-T,{selected}", "yes"),
            ):
                with self.subTest(network=network):
                    result = subprocess.run(
                        [
                            str(PROJECT_ROOT / "bin/podman"),
                            "run",
                            "--rm",
                            "--init",
                            "--timeout=45",
                            "--user=pwuser",
                            "--security-opt=seccomp=chromium",
                            "--shm-size=256m",
                            f"--network={network}",
                            os.environ.get(
                                "PARANOID_PODMAN_CHROMIUM_IMAGE",
                                "localhost/paranoid-podman-chromium-test:dev",
                            ),
                            "node",
                            "--input-type=module",
                            "-e",
                            NETWORK_PROBE,
                            str(selected),
                            str(other),
                            enabled,
                        ],
                        cwd=PROJECT_ROOT,
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=60,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    evidence = json.loads(result.stdout)
                    self.assertEqual(evidence["enabled"], enabled == "yes")
                    if enabled == "yes":
                        self.assertGreater(evidence["pngBytes"], 100)
                    print(json.dumps(evidence, sort_keys=True))
