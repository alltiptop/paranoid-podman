"""Opt-in browser proof in an existing image; no pulls, mounts, or host ports."""

import json
import os
import subprocess
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

BROWSER_PROBE = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { chromium } from 'playwright-core';

const status = (pid) => Object.fromEntries(
  fs.readFileSync(`/proc/${pid}/status`, 'utf8').trim().split('\n')
    .map(line => [line.slice(0, line.indexOf(':')), line.slice(line.indexOf(':') + 1).trim()])
);
const parent = status('self');
assert.notEqual(process.getuid(), 0);
for (const key of ['CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb']) {
  assert.equal(BigInt(`0x${parent[key]}`), 0n, key);
}
assert.equal(parent.NoNewPrivs, '1');
assert.equal(parent.Seccomp, '2');
const browser = await chromium.launch({
  headless: true,
  chromiumSandbox: true,
  timeout: 20000,
  args: ['--use-gl=angle', '--use-angle=swiftshader'],
});
try {
  const page = await browser.newPage({ viewport: { width: 320, height: 200 } });
  await page.setContent('<html><body><h1>Sandbox check</h1><canvas></canvas></body></html>');
  const webgl = await page.evaluate(() => Boolean(document.querySelector('canvas').getContext('webgl2')));
  assert.equal(webgl, true, 'software WebGL2');
  const png = await page.screenshot();
  assert.equal(png.subarray(1, 4).toString(), 'PNG');
  const renderers = [];
  for (const pid of fs.readdirSync('/proc').filter(name => /^\d+$/.test(name))) {
    let args;
    // Chromium rewrites child process titles into a single argv string.
    try { args = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split(/[\0\s]+/); }
    catch { continue; }
    if (!args.includes('--type=renderer')) continue;
    assert.equal(args.includes('--no-sandbox'), false);
    const child = status(pid);
    assert.equal(child.NoNewPrivs, '1');
    assert.equal(child.Seccomp, '2');
    assert.ok(Number(child.Seccomp_filters) > Number(parent.Seccomp_filters), 'browser seccomp filter');
    assert.ok(child.NSpid.split(/\s+/).length > parent.NSpid.split(/\s+/).length, 'browser PID namespace');
    renderers.push({ pid, filters: child.Seccomp_filters, namespacePids: child.NSpid });
  }
  assert.ok(renderers.length > 0, 'sandboxed renderer found');
  console.log(JSON.stringify({ uid: process.getuid(), capabilities: parent.CapBnd,
    noNewPrivileges: parent.NoNewPrivs, seccomp: parent.Seccomp,
    browser: browser.version(), webgl, pngBytes: png.length, renderers }));
} finally {
  await browser.close();
}
"""


@unittest.skipUnless(
    os.environ.get("PARANOID_PODMAN_RUN_CHROMIUM_TESTS") == "1",
    "set PARANOID_PODMAN_RUN_CHROMIUM_TESTS=1 for the existing-image browser test",
)
class ChromiumContainerTests(unittest.TestCase):
    def test_sandbox_with_dropped_capabilities_and_software_rendering(self):
        environment = {
            **os.environ,
            "PODMAN_GUARD_INSTALLATION_ID": uuid.uuid4().hex + uuid.uuid4().hex,
        }
        result = subprocess.run(
            [
                str(PROJECT_ROOT / "bin/podman"),
                "run",
                "--rm",
                "--init",
                "--network=none",
                "--timeout=45",
                "--user=pwuser",
                "--security-opt=seccomp=chromium",
                "--shm-size=256m",
                os.environ.get("PARANOID_PODMAN_CHROMIUM_IMAGE", "paranoid-podman-chromium-test:dev"),
                "node",
                "--input-type=module",
                "-e",
                BROWSER_PROBE,
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
        self.assertTrue(evidence["webgl"])
        self.assertGreater(evidence["pngBytes"], 100)
        print(json.dumps(evidence, sort_keys=True))
