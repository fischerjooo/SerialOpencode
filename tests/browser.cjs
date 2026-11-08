// Run with: node tests/browser.cjs (Playwright and Chromium required).
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const http = require("node:http"),
  fs = require("node:fs"),
  path = require("node:path");
(async () => {
  const server = http
    .createServer((req, res) => {
      const file = path.join(
        __dirname,
        "..",
        req.url === "/" ? "index.html" : req.url,
      );
      try {
        res.end(fs.readFileSync(file));
      } catch {
        res.writeHead(404);
        res.end();
      }
    })
    .listen(0, "127.0.0.1");
  await new Promise((r) => server.on("listening", r));
  let browser;
  try {
    browser = await chromium.launch({
      executablePath: process.env.CHROMIUM_PATH || undefined,
      headless: true,
      args: ["--no-sandbox"],
    });
  } catch (e) {
    server.close();
    throw e;
  }
  try {
    const page = await browser.newPage({
      viewport: { width: 390, height: 844 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.addInitScript(() => {
      let controller;
      window.sent = [];
      window.emit = (e) =>
        controller.enqueue(
          new TextEncoder().encode("@OCBT1 " + JSON.stringify(e) + "\r\n"),
        );
      const port = {
        open: async () => {},
        close: async () => {},
        readable: new ReadableStream({
          start(c) {
            controller = c;
          },
        }),
        writable: new WritableStream({
          write(bytes) {
            const line = new TextDecoder().decode(bytes).trim();
            window.sent.push(line);
            if (line === "/ui json") emit({ type: "hello", version: 3 });
            if (line === "/sessions")
              emit({
                type: "sessions",
                items: [{ index: 1, id: "s1", title: "Project Alpha" }],
              });
            if (line === "/use 1") {
              emit({ type: "selected", id: "s1", title: "Project Alpha" });
              emit({ type: "history_begin", sessionId: "s1" });
              emit({
                type: "message",
                id: "m1",
                sessionId: "s1",
                role: "assistant",
                text: "Hello",
              });
              emit({ type: "history_end", sessionId: "s1" });
            }
            if (line.startsWith("@OCBT1 ")) {
              const r = JSON.parse(line.slice(7));
              if (r.action === "catalog")
                emit({
                  type: "result",
                  requestId: r.requestId,
                  ok: true,
                  data: {
                    agents: ["build", "plan"],
                    models: [
                      {
                        providerID: "test",
                        modelID: "model",
                        name: "Test model",
                      },
                    ],
                  },
                });
              else if (r.action === "prompt" && r.text === "FAIL")
                emit({
                  type: "result",
                  requestId: r.requestId,
                  ok: false,
                  error: "Server unavailable",
                });
              else emit({ type: "result", requestId: r.requestId, ok: true });
            }
          },
        }),
      };
      Object.defineProperty(navigator, "serial", {
        value: { getPorts: async () => [], requestPort: async () => port },
      });
    });
    await page.goto("http://127.0.0.1:" + server.address().port);
    await page.click("#connectBtn");
    await page.waitForFunction(
      () =>
        document.querySelector("#sessionTitle").textContent === "Project Alpha",
    );
    await page.fill("#prompt", "first line\nsecond line 👋");
    await page.click("#sendBtn");
    await page.waitForFunction(
      () => document.querySelector("#prompt").value === "",
    );
    const prompt = await page.evaluate(() =>
      sent
        .filter((x) => x.startsWith("@OCBT1 "))
        .map((x) => JSON.parse(x.slice(7)))
        .find((x) => x.action === "prompt"),
    );
    assert.equal(prompt.text, "first line\nsecond line 👋");
    assert.equal(prompt.sessionId, "s1");
    await page.fill("#prompt", "FAIL");
    await page.click("#sendBtn");
    await page.waitForFunction(
      () =>
        document.querySelector("#notification").textContent ===
        "Server unavailable",
    );
    assert.equal(await page.inputValue("#prompt"), "FAIL");
    await page.evaluate(() =>
      emit({
        type: "message_delta",
        id: "m1",
        sessionId: "s1",
        role: "assistant",
        text: " world",
      }),
    );
    await page.waitForFunction(
      () => document.querySelector(".msg-body").textContent === "Hello world",
    );
    await page.evaluate(() =>
      emit({
        type: "message",
        id: "old",
        sessionId: "other",
        role: "assistant",
        text: "wrong session",
      }),
    );
    assert.equal(await page.locator(".msg").count(), 1);
    await page.evaluate(() =>
      emit({
        type: "pending",
        sessionId: "s1",
        permission: [{ id: "p1", permission: "bash", patterns: ["npm test"] }],
      }),
    );
    await page.getByText("Allow once", { exact: true }).click();
    await page.waitForFunction(() => !document.querySelector(".request-card"));
    await page.click("#stopBtn");
    assert(
      await page.evaluate(() =>
        sent.some((x) => x.includes('"action":"abort"')),
      ),
    );
    await page.click("#menuBtn");
    await page.fill("#sessionSearch", "missing");
    assert.equal(await page.locator(".session").count(), 0);
    await page.fill("#sessionSearch", "Alpha");
    assert.equal(await page.locator(".session").count(), 1);
    await page.click("#closeSidebarBtn");
    for (const width of [360, 390, 768, 1280]) {
      await page.setViewportSize({ width, height: 844 });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        "overflow at " + width,
      );
      const box = await page.locator("#prompt").boundingBox();
      assert(box.height > 0 && box.y + box.height <= 844, "composer clipped");
    }
    await page.setViewportSize({ width: 390, height: 844 });
    fs.mkdirSync(".work", { recursive: true });
    await page.screenshot({ path: ".work/mobile.png", fullPage: true });
    assert.deepEqual(errors, []);
    console.log(
      "Browser integration passed: multiline, acknowledgements, retained failures, delta rendering, stale events, permission replies, stop, search, responsive layout.",
    );
  } finally {
    await browser.close();
    server.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
