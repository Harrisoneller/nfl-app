import assert from "node:assert/strict";
import { test } from "node:test";

import { buildHeaders, mergeRequestInit } from "./request-headers.ts";

test("caller headers keep the stored token once", () => {
  const headers = buildHeaders(
    { Accept: "application/json", Authorization: "Bearer caller-token" },
    "stored-token",
  );
  const authKeys = Object.keys(headers).filter((key) => key.toLowerCase() === "authorization");
  assert.deepEqual(authKeys, ["Authorization"]);
  assert.equal(headers.Authorization, "Bearer stored-token");
  assert.equal(headers.Accept, "application/json");
  assert.equal(headers["content-type"], "application/json");
});

test("a Headers object cannot duplicate authorization", () => {
  const extra = new Headers();
  extra.set("authorization", "Bearer caller-token");
  extra.set("X-Trace", "abc");
  const headers = buildHeaders(extra, "stored-token");
  const authKeys = Object.keys(headers).filter((key) => key.toLowerCase() === "authorization");
  assert.deepEqual(authKeys, ["Authorization"]);
  assert.equal(headers.Authorization, "Bearer stored-token");
  assert.equal(headers["x-trace"], "abc");
});

test("caller authorization is kept when there is no stored token", () => {
  const headers = buildHeaders({ Authorization: "Bearer caller-token" }, null);
  assert.equal(headers.Authorization, "Bearer caller-token");
  assert.equal(
    Object.keys(headers).filter((key) => key.toLowerCase() === "authorization").length,
    1,
  );
});

test("spreading caller init does not drop the merged authorization", () => {
  const merged = mergeRequestInit(
    { cache: "no-store" },
    {
      method: "POST",
      headers: { "X-Trace": "1", Authorization: "Bearer other" },
    },
    "stored-token",
  );
  const headers = merged.headers as Record<string, string>;
  assert.equal(merged.method, "POST");
  assert.equal(merged.cache, "no-store");
  assert.equal(headers.Authorization, "Bearer stored-token");
  assert.equal(headers["X-Trace"], "1");
  assert.equal(
    Object.keys(headers).filter((key) => key.toLowerCase() === "authorization").length,
    1,
  );
});
