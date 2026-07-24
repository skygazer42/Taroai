import test from "node:test";
import assert from "node:assert/strict";

import { resolveAccountIdentity, resolveGreetingFontSize } from "../assets/account-identity.js";

test("account identity prefers the registered name", () => {
  assert.deepEqual(resolveAccountIdentity("New User", "new@example.com"), {
    name: "New User",
    shortName: "New",
    initials: "NU",
  });
  assert.equal(resolveAccountIdentity("张三", "zhang@example.com").shortName, "张三");
  assert.equal(resolveAccountIdentity("207829897", "207829897@qq.com").shortName, "2078");
  assert.equal(resolveAccountIdentity("", "luke@example.com").name, "luke");
  assert.equal(resolveAccountIdentity("averyveryverylongusername", "long@example.com").shortName, "averyveryverylo…");
  assert.equal(resolveGreetingFontSize("2078"), 72);
  assert.equal(resolveGreetingFontSize("skygazer42"), 54);
});
