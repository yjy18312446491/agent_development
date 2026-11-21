// Node built-ins only: execute the real inline panel script against a small DOM.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "../../app/static/demo.html"), "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
const script = scripts.at(-1)[1];
const cases = ["backend", "junior", "frontend", "other"].map(name => ({
  case_id: `demo_${name}`, title: `<img src=x onerror=bad()> ${name}`, text: `<script>bad()</script> ${name}`
}));
const ready = { enabled: true, mode: "fixed-fake-vectors", cases };
const success = {
  advisory: true, mode: "fixed-fake-vectors", quality_conclusion: "NOT_ESTABLISHED", status: "OK",
  hits: [{ reference_jd_id: "JD3", title: "<img src=x onerror=bad()>", similarity: 0.5,
           evidence_excerpt: "<script>bad()</script>" }]
};

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.textContent = "";
    this.value = "";
    this.disabled = true;
    this.listeners = {};
  }
  set innerHTML(value) { throw Error("HTML injection is forbidden"); }
  append(...nodes) {
    for (const node of nodes) {
      if (node.tag === "fragment") this.children.push(...node.children);
      else this.children.push(node);
    }
  }
  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
    if (this.tag === "select") this.value = this.children[0]?.value || "";
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
}

async function panel(status = ready, evidence = success, options = {}) {
  const elements = Object.fromEntries([
    "advisory-case", "advisory-query", "advisory-text", "advisory-notice", "advisory-hits", "advisory-form"
  ].map(id => [id, new Element(id === "advisory-case" ? "select" : "div")]));
  const requests = [];
  const context = vm.createContext({
    document: {
      querySelector(selector) {
        assert.ok(selector.startsWith("#advisory-"), "Must not access batch controls");
        assert.ok(elements[selector.slice(1)]);
        return elements[selector.slice(1)];
      },
      createElement: tag => new Element(tag),
      createDocumentFragment: () => new Element("fragment")
    },
    fetch: async (url, request) => {
      requests.push({ url, request });
      assert.ok(["/advisory/status", "/advisory/evidence"].includes(url));
      if (options.fail === url) throw Error("private internal exception");
      const data = url.endsWith("status") ? status : evidence;
      return { ok: url.endsWith("status") || !options.review, json: async () => data };
    },
    requestBatch: () => assert.fail("Must not call batch uploader"),
    renderRows: () => assert.fail("Must not update scoring rows")
  });
  vm.runInContext(script, context);
  await new Promise(resolve => setImmediate(resolve));
  return { elements, requests, query: () => vm.runInContext("queryAdvisory()", context) };
}

test("disabled status leaves controls disabled and never posts", async () => {
  const p = await panel({ enabled: false, mode: "disabled" });
  await p.query();
  assert.equal(p.requests.length, 1);
  assert.equal(p.elements["advisory-query"].disabled, true);
  assert.equal(p.elements["advisory-case"].disabled, true);
});

test("enabled panel sends only fixed case/top_k and renders unsafe text literally", async () => {
  const p = await panel();
  assert.equal(p.requests.length, 1, "Status must not trigger an evidence query");
  assert.equal(p.elements["advisory-case"].children.length, 4);
  assert.equal(p.elements["advisory-text"].textContent, cases[0].text);
  await p.query();
  assert.equal(p.requests.length, 2);
  assert.deepEqual(JSON.parse(p.requests[1].request.body), { case_id: "demo_backend", top_k: 3 });
  const hit = p.elements["advisory-hits"].children[0];
  assert.equal(hit.children[0].textContent, "JD3 · <img src=x onerror=bad()>");
  assert.equal(hit.children[2].textContent, "<script>bad()</script>");
  assert.equal(p.elements["advisory-query"].disabled, false);
});

test("case changes clear stale evidence and cannot submit batch form", async () => {
  const p = await panel();
  await p.query();
  p.elements["advisory-case"].value = "demo_other";
  p.elements["advisory-case"].listeners.change();
  assert.equal(p.elements["advisory-hits"].children.length, 0);
  assert.equal(p.elements["advisory-text"].textContent, cases[3].text);
  let prevented = false;
  p.elements["advisory-form"].listeners.submit({ preventDefault() { prevented = true; } });
  assert.ok(prevented);
});

test("review and network errors clear evidence without exposing errors", async () => {
  for (const options of [{ review: true }, { fail: "/advisory/evidence" }]) {
    const p = await panel(ready, { ...success, status: "REVIEW", hits: [], detail: "private" }, options);
    await p.query();
    assert.equal(p.elements["advisory-hits"].children.length, 0);
    assert.ok(!p.elements["advisory-notice"].textContent.includes("private"));
    assert.equal(p.elements["advisory-query"].disabled, false);
  }
});

test("status failures stay disabled and repeated clicks do not duplicate requests", async () => {
  const failed = await panel(ready, success, { fail: "/advisory/status" });
  await failed.query();
  assert.equal(failed.requests.length, 1);
  assert.equal(failed.elements["advisory-query"].disabled, true);
  const p = await panel();
  await Promise.all([p.query(), p.query()]);
  assert.equal(p.requests.length, 2);
});

test("panel is separate and always identifies fixed vectors / unestablished quality", () => {
  assert.ok(html.includes("合成样例证据演示"));
  assert.ok(html.includes("固定向量演示"));
  assert.ok(html.includes("语义质量尚未验证"));
  assert.ok(html.includes('id="advisory-query" type="button" disabled'));
  assert.ok(!script.includes("innerHTML"));
});
