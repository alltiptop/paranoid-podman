import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';

const browser = await chromium.launch({
  headless: true,
  chromiumSandbox: true,
  args: ['--use-gl=angle', '--use-angle=swiftshader'],
});
try {
  const page = await browser.newPage({ viewport: { width: 320, height: 200 } });
  await page.setContent('<h1>Browser sandbox fixture</h1><canvas></canvas>');
  const webgl = await page.evaluate(() => Boolean(document.querySelector('canvas').getContext('webgl2')));
  assert.equal(webgl, true);
  const png = await page.screenshot();
  assert.equal(png.subarray(1, 4).toString(), 'PNG');
  console.log(JSON.stringify({ browser: browser.version(), webgl, pngBytes: png.length }));
} finally {
  await browser.close();
}
