// A small Chrome DevTools Protocol driver: enough to open pages in headless Chrome, set the
// viewport and color scheme, run a step, and take a screenshot. Node.js 22, no packages.
//
// CHROME picks the browser; otherwise the usual Chrome, Chromium, Edge, or Brave locations
// are tried.

import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CANDIDATES = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/usr/bin/microsoft-edge",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
];

export function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const found = CANDIDATES.find((path) => existsSync(path));
  if (!found) throw new Error("No Chrome found; set CHROME to a Chrome, Chromium, Edge, or Brave binary.");
  return found;
}

export async function launch() {
  const profile = mkdtempSync(join(tmpdir(), "tessera-capture-"));
  const chrome = spawn(findChrome(), [
    "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
    "--no-default-browser-check", "--hide-scrollbars", "--disable-extensions", "--mute-audio",
    "--font-render-hinting=none", "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  const url = await new Promise((resolve, reject) => {
    let text = "";
    const timer = setTimeout(() => reject(new Error("Chrome did not start")), 15000);
    chrome.stderr.on("data", (chunk) => {
      text += chunk;
      const match = text.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
    chrome.on("exit", (code) => reject(new Error(`Chrome exited (${code})`)));
  });
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let next = 0;
  const pending = new Map();
  const listeners = new Set();
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(`${message.error.message} (${message.error.code})`));
      else resolve(message.result);
    } else {
      for (const listener of listeners) listener(message);
    }
  };
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++next;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });

  async function page() {
    const { targetId } = await send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
    const call = (method, params) => send(method, params, sessionId);
    await call("Page.enable");
    await call("Runtime.enable");
    const event = (name, timeout = 30000) => new Promise((resolve, reject) => {
      const timer = setTimeout(() => { listeners.delete(listen); reject(new Error(`timed out waiting for ${name}`)); }, timeout);
      const listen = (m) => {
        if (m.sessionId === sessionId && m.method === name) { clearTimeout(timer); listeners.delete(listen); resolve(m.params); }
      };
      listeners.add(listen);
    });
    return {
      async viewport({ width, height, scale = 1.5, mobile = false }) {
        await call("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: scale, mobile });
      },
      async media(features) {
        await call("Emulation.setEmulatedMedia", { features: Object.entries(features).map(([name, value]) => ({ name, value })) });
      },
      async goto(url) {
        const loaded = event("Page.loadEventFired");
        await call("Page.navigate", { url });
        await loaded;
      },
      async evaluate(expression) {
        const { result, exceptionDetails } = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
        if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
        return result.value;
      },
      /** Wait until an expression is truthy (polling), or fail after ``timeout`` ms. */
      async until(expression, timeout = 20000) {
        const start = Date.now();
        while (Date.now() - start < timeout) {
          if (await this.evaluate(`Boolean(${expression})`)) return;
          await new Promise((r) => setTimeout(r, 100));
        }
        throw new Error(`timed out waiting for ${expression}`);
      },
      async screenshot({ format = "webp", quality = 80 } = {}) {
        const { data } = await call("Page.captureScreenshot", { format, quality, captureBeyondViewport: false });
        return Buffer.from(data, "base64");
      },
      async close() {
        await send("Target.closeTarget", { targetId });
      },
      /** Any DevTools Protocol command, for what the methods above do not cover. */
      cdp: call,
      /** Press a key, as the keyboard would (``Tab``; ``shift`` for Shift+Tab). */
      async key(key, { shift = false } = {}) {
        const codes = { Tab: 9, Enter: 13, Escape: 27 };
        const base = { key, code: key, windowsVirtualKeyCode: codes[key] ?? 0, modifiers: shift ? 8 : 0 };
        await call("Input.dispatchKeyEvent", { type: "keyDown", ...base });
        await call("Input.dispatchKeyEvent", { type: "keyUp", ...base });
      },
    };
  }

  async function close() {
    try { socket.close(); } catch { /* already closed */ }
    chrome.kill();
    await new Promise((r) => setTimeout(r, 200));
    rmSync(profile, { recursive: true, force: true });
  }
  return { page, close };
}
