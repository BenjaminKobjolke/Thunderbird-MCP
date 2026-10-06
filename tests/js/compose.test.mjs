import assert from "node:assert/strict";
import { it } from "node:test";
import { fakeBrowser, fakeLog, loadScript } from "./harness.mjs";

function setup() {
  const registry = loadScript("background/registry.js");
  const browser = fakeBrowser({ folders: [{ id: "drafts", path: "/Drafts", specialUse: ["drafts"], isUnified: false }] });
  const calls = [];
  const handlers = new Map();
  let current = {};
  browser.tbx.invoke = async () => ({ name: "pic.jpg", contentType: "image/jpeg", base64: "YWJj" });
  browser.messages = {
    saveMessage: async () => { calls.push("headlessSave"); return { messages: [] }; },
    sendMessage: async () => { calls.push("headlessSend"); return { messages: [] }; },
  };
  browser.compose = {
    beginNew: async (details) => { calls.push("beginNew"); current = details; return { id: 1 }; },
    beginReply: async (_, __, details) => { calls.push("beginReply"); current = { ...details, body: "<body>quote</body>" }; return { id: 1 }; },
    setComposeDetails: async (_, patch) => { current = { ...current, ...patch }; },
    getComposeDetails: async () => current,
    saveMessage: async () => ({ messages: [] }),
    sendMessage: async () => ({ messages: [] }),
  };
  browser.tabs = { remove: async () => {} };
  loadScript("background/handlers/compose.js", {
    browser, tbxError: registry.tbxError, tbxUtil: registry.tbxUtil,
    tbxRegistry: { define: (name, fn) => handlers.set(name, fn) },
    tbxLog: fakeLog(), setTimeout: (fn) => fn(),
  });
  return { handlers, calls, browser, current: () => current };
}

it("embeds every named image reference and uses a compose window", async () => {
  const { handlers, calls, current } = setup();
  const result = await handlers.get("compose.save")({
    subject: "photo", isHtml: true,
    body: '<img src="cid:pic"><img src="cid:pic"><img src="cid:pic2">',
    inlineImages: [{ cid: "pic", path: "C:/pic.jpg" }, { cid: "pic2", path: "C:/pic2.jpg" }],
  });
  assert.deepEqual(calls, ["beginNew"]);
  assert.equal(result.transport, "composeWindow");
  assert.equal((current().body.match(/data:image\/jpeg/g) || []).length, 3);
  assert.doesNotMatch(current().body, /cid:/);
});

it("rejects invalid inline images before opening a window", async () => {
  for (const params of [
    { isHtml: false, body: '<img src="cid:pic">' },
    { isHtml: true, body: "no image" },
  ]) {
    const { handlers, calls } = setup();
    await assert.rejects(handlers.get("compose.save")({
      ...params, inlineImages: [{ cid: "pic", path: "C:/pic.jpg" }],
    }), (error) => error.tbxKind === "usage");
    assert.deepEqual(calls, []);
  }
});

it("routes raw data images through a window", async () => {
  const { handlers, calls } = setup();
  await handlers.get("compose.send")({ mode: "send", isHtml: true,
    body: '<img src="data:image/png;base64,YWJj">' });
  assert.deepEqual(calls, ["beginNew"]);
});

it("forces an inline image reply into HTML", async () => {
  const { handlers, calls, current } = setup();
  await handlers.get("compose.reply")({ messageId: 5, isHtml: true,
    body: '<img src="cid:pic">', inlineImages: [{ cid: "pic", path: "C:/pic.jpg" }] });
  assert.deepEqual(calls, ["beginReply"]);
  assert.equal(current().isPlainText, false);
  assert.match(current().body, /data:image\/jpeg/);
});

it("rejects a non-image file", async () => {
  const { handlers, browser, calls } = setup();
  browser.tbx.invoke = async () => ({ name: "notes.txt", contentType: "text/plain", base64: "YWJj" });
  await assert.rejects(handlers.get("compose.save")({ isHtml: true,
    body: '<img src="cid:pic">', inlineImages: [{ cid: "pic", path: "C:/notes.txt" }],
  }), (error) => error.tbxKind === "usage" && /image file/.test(error.message));
  assert.deepEqual(calls, []);
});
