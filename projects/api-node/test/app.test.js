import { test, after, before } from "node:test";
import assert from "node:assert/strict";
import { createApp, sum } from "../src/app.js";

test("sum adds numbers", () => {
  assert.equal(sum([1, 2, 3]), 6);
  assert.equal(sum([]), 0);
});

test("sum rejects non-numbers", () => {
  assert.throws(() => sum(["1"]), TypeError);
});

let server;
let base;
before(async () => {
  server = createApp().listen(0);
  await new Promise((resolve) => server.once("listening", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(() => server.close());

test("GET /health", async () => {
  const res = await fetch(`${base}/health`);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { status: "ok" });
});

test("GET /sum", async () => {
  const res = await fetch(`${base}/sum?n=1,2,39`);
  assert.deepEqual(await res.json(), { result: 42 });
});

test("GET /sum with bad input is 400", async () => {
  const res = await fetch(`${base}/sum?n=1,x`);
  assert.equal(res.status, 400);
});
