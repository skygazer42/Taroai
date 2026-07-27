import test from "node:test";
import assert from "node:assert/strict";

import { opsStatusbarModel } from "../assets/ops-status.js";

const IDLE_STAGES = ["waiting", "waiting", "waiting", "waiting", "waiting"];

test("no run shows an idle empty statusbar", () => {
  const model = opsStatusbarModel({
    hasRun: false,
    failed: false,
    terminal: false,
    pendingApproval: false,
    stageStates: IDLE_STAGES,
  });
  assert.deepEqual(model, { progress: 0, progressState: "running", dotState: "idle" });
});

test("progress counts done stages fully and active stages half", () => {
  const model = opsStatusbarModel({
    hasRun: true,
    failed: false,
    terminal: false,
    pendingApproval: false,
    stageStates: ["done", "done", "active", "waiting", "waiting"],
  });
  assert.equal(model.progress, 50);
  assert.equal(model.dotState, "live");
});

test("pending approval wins over every other dot state", () => {
  const model = opsStatusbarModel({
    hasRun: true,
    failed: true,
    terminal: true,
    pendingApproval: true,
    stageStates: ["done", "failed", "waiting", "waiting", "waiting"],
  });
  assert.equal(model.dotState, "warn");
});

test("failed run reports error dot and error progress", () => {
  const model = opsStatusbarModel({
    hasRun: true,
    failed: true,
    terminal: true,
    pendingApproval: false,
    stageStates: ["done", "failed", "waiting", "waiting", "waiting"],
  });
  assert.equal(model.dotState, "error");
  assert.equal(model.progressState, "error");
  assert.equal(model.progress, 30);
});

test("successful terminal run fills the bar and turns it green", () => {
  const model = opsStatusbarModel({
    hasRun: true,
    failed: false,
    terminal: true,
    pendingApproval: false,
    stageStates: ["done", "done", "done", "waiting", "done"],
  });
  assert.deepEqual(model, { progress: 100, progressState: "done", dotState: "done" });
});
