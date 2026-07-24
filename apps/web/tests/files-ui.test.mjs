import assert from "node:assert/strict";
import test from "node:test";

import { fileKind } from "../assets/files-ui.js";

test("OpenXML documents are binary while real XML stays text", () => {
  assert.equal(fileKind({ content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }), "binary");
  assert.equal(fileKind({ content_type: "application/xml" }), "text");
});
