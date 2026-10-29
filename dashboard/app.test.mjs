import { test } from "node:test";
import assert from "node:assert/strict";
import {
  latestRun, isFlagged, urgencySort, buildGrid, parsePath, mrUrl, groupOf, escapeHtml,
} from "./app.js";

// Builds a record with sane, unflagged defaults.
function rec(over = {}) {
  return {
    mr_iid: 1, commit_sha: "abc", timestamp: "2026-10-01T10:00:00Z",
    test_passed: 5, test_total: 5, summary: "ok", comments: [],
    rubric_scores: { correctness: { score: 4, max: 4 } },
    recommended_score: 0.9, needs_human: false, needs_human_reason: "",
    integrity_flag: false, ...over,
  };
}

test("latestRun picks the newest timestamp, not the last array slot", () => {
  const a = rec({ timestamp: "2026-10-02T00:00:00Z", mr_iid: 2 });
  const b = rec({ timestamp: "2026-10-01T00:00:00Z", mr_iid: 1 });
  assert.equal(latestRun([a, b]).mr_iid, 2);
  assert.equal(latestRun([]), null);
});

test("isFlagged: clean record is not flagged", () => {
  assert.equal(isFlagged(rec()), false);
});

test("isFlagged: needs_human", () => {
  assert.equal(isFlagged(rec({ needs_human: true })), true);
});

test("isFlagged: score below 0.6 flags, exactly 0.6 does not", () => {
  assert.equal(isFlagged(rec({ recommended_score: 0.59 })), true);
  assert.equal(isFlagged(rec({ recommended_score: 0.6 })), false);
});

test("isFlagged: test/correctness disagreement above 0.2 flags", () => {
  // tests 1.0 vs correctness 0.5
  const r = rec({ rubric_scores: { correctness: { score: 2, max: 4 } } });
  assert.equal(isFlagged(r), true);
});

test("isFlagged: disagreement exactly 0.2 does not flag", () => {
  // tests 0.5 vs 0.7 -> diff 0.2 (floating point: 0.7-0.5 = 0.19999999999999996)
  const r = rec({ test_passed: 5, test_total: 10,
    rubric_scores: { correctness: { score: 7, max: 10 } } });
  assert.equal(isFlagged(r), false);
});

test("isFlagged: skips comparison when no tests or no correctness", () => {
  const wide = { correctness: { score: 0, max: 4 } };
  assert.equal(isFlagged(rec({ test_total: 0, test_passed: 0, rubric_scores: wide })), false);
  assert.equal(isFlagged(rec({ rubric_scores: { style: { score: 0, max: 4 } } })), false);
  assert.equal(isFlagged(rec({ rubric_scores: { correctness: { score: 0, max: 0 } } })), false);
});

test("urgencySort: integrity first, then needs_human, then lowest score", () => {
  const rows = [
    { netid: "low", run: rec({ recommended_score: 0.2 }) },
    { netid: "human", run: rec({ needs_human: true, recommended_score: 0.9 }) },
    { netid: "integ", run: rec({ integrity_flag: true, recommended_score: 0.95 }) },
    { netid: "low2", run: rec({ recommended_score: 0.4 }) },
  ];
  assert.deepEqual(urgencySort(rows).map((r) => r.netid), ["integ", "human", "low", "low2"]);
  assert.equal(rows[0].netid, "low", "input is not mutated");
});

test("parsePath: slug/netid.json, ignores everything else", () => {
  assert.deepEqual(parsePath("01-warmup/abc123.json"), { assignment: "01-warmup", netid: "abc123" });
  assert.equal(parsePath("README.md"), null);
  assert.equal(parsePath("a/b/c.json"), null);
  assert.equal(parsePath("01-warmup/notes.txt"), null);
});

test("buildGrid: students x assignments, empty cells missing", () => {
  const files = {
    "01-warmup/aaa.json": [rec({ recommended_score: 0.5 }), rec({ recommended_score: 0.8, timestamp: "2026-10-03T00:00:00Z" })],
    "02-next/aaa.json": [rec()],
    "01-warmup/bbb.json": [rec({ needs_human: true })],
  };
  const g = buildGrid(files);
  assert.deepEqual(g.assignments, ["01-warmup", "02-next"]);
  assert.deepEqual(g.students.map((s) => s.netid), ["aaa", "bbb"]);
  assert.equal(g.cell("aaa", "01-warmup").run.recommended_score, 0.8);
  assert.equal(g.cell("aaa", "01-warmup").flagged, false);
  assert.equal(g.cell("bbb", "01-warmup").flagged, true);
  assert.equal(g.cell("bbb", "02-next"), null);
});

test("mrUrl and groupOf", () => {
  assert.equal(groupOf("duke-cs-claude-fa26/staff/results"), "duke-cs-claude-fa26");
  assert.equal(
    mrUrl("https://gitlab.example.edu/", "g/staff/results", "abc", 7),
    "https://gitlab.example.edu/g/students/abc/-/merge_requests/7");
});

test("escapeHtml neutralises markup", () => {
  assert.equal(escapeHtml(`<img src=x onerror="a">&'`), "&lt;img src=x onerror=&quot;a&quot;&gt;&amp;&#39;");
});

test("isFlagged: integrity_flag", () => {
  assert.equal(isFlagged(rec({ integrity_flag: true })), true);
});
