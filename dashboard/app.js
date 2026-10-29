// Instructor dashboard: reads review results from the staff/results GitLab project.
// Pure logic first (tested by app.test.mjs), thin DOM code at the bottom.

// Keep in sync with tooling/review/flag.py
const MIN_SCORE = 0.6;
const MAX_DISAGREEMENT = 0.2;

// Mirrors should_flag() in tooling/review/flag.py, boundaries included.
// A failed run (status "failed") has no review fields, so a human must look.
export function isFlagged(run) {
  if (run.status === "failed") return true;
  if (run.integrity_flag) return true;
  if (run.needs_human) return true;
  if (run.recommended_score < MIN_SCORE) return true;
  const c = (run.rubric_scores || {}).correctness;
  // No tests ran, or no correctness criterion: nothing to compare against.
  if (run.test_total && c && c.max) {
    const testScore = run.test_passed / run.test_total;
    if (Math.abs(testScore - c.score / c.max) > MAX_DISAGREEMENT) return true;
  }
  return false;
}

// ISO 8601 strings in the same format sort correctly as text.
export function latestRun(records) {
  if (!records.length) return null;
  return records.reduce((a, b) => (b.timestamp >= a.timestamp ? b : a));
}

// rows: [{netid, run, ...}]. Integrity first, then needs_human or failed, then lowest score.
export function urgencySort(rows) {
  const rank = (r) => (r.run.integrity_flag ? 0 : r.run.needs_human || r.run.status === "failed" ? 1 : 2);
  const score = (r) => r.run.recommended_score ?? 0; // failed runs have no score
  return [...rows].sort((a, b) => rank(a) - rank(b) || score(a) - score(b));
}

// "01-warmup/abc123.json" -> {assignment, netid}; anything else -> null.
export function parsePath(path) {
  const m = /^([^/]+)\/([^/]+)\.json$/.exec(path);
  return m ? { assignment: m[1], netid: m[2] } : null;
}

// files: {path: [records]}. Returns assignments, students and a cell lookup.
export function buildGrid(files) {
  const cells = new Map();
  const assignments = new Set();
  const netids = new Set();
  for (const [path, records] of Object.entries(files)) {
    const p = parsePath(path);
    const run = p && latestRun(records);
    if (!run) continue;
    assignments.add(p.assignment);
    netids.add(p.netid);
    cells.set(`${p.netid}|${p.assignment}`, { run, flagged: isFlagged(run), records });
  }
  return {
    assignments: [...assignments].sort(),
    students: [...netids].sort().map((netid) => ({ netid })),
    cell: (netid, assignment) => cells.get(`${netid}|${assignment}`) || null,
  };
}

// Results project is <group>/staff/results, so the group is everything before /staff.
export function groupOf(projectPath) {
  return projectPath.replace(/\/staff\/results$/, "");
}

export function mrUrl(gitlabUrl, projectPath, netid, iid) {
  const base = gitlabUrl.replace(/\/+$/, "");
  return `${base}/${groupOf(projectPath)}/students/${netid}/-/merge_requests/${iid}`;
}

// Student-influenced text (summary, reasons) must never reach innerHTML raw.
export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Splits arr into batches of at most n, so we don't open 100 requests at once.
export function chunk(arr, n) {
  const out = [];
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n));
  return out;
}

// ---- GitLab API ----

async function api(cfg, path) {
  const res = await fetch(`${cfg.url}/api/v4/projects/${encodeURIComponent(cfg.project)}${path}`,
    { headers: { "PRIVATE-TOKEN": cfg.token } });
  if (!res.ok) throw new Error(`GitLab ${res.status} for ${path}`);
  return res;
}

async function listJsonPaths(cfg) {
  const paths = [];
  for (let page = "1"; page;) {
    const res = await api(cfg, `/repository/tree?recursive=true&per_page=100&page=${page}`);
    for (const item of await res.json()) {
      if (item.type === "blob" && parsePath(item.path)) paths.push(item.path);
    }
    page = res.headers.get("x-next-page"); // empty on the last page
  }
  return paths;
}

export async function fetchResults(cfg) {
  const files = {};
  const paths = await listJsonPaths(cfg);
  const branch = (await (await api(cfg, "")).json()).default_branch;
  for (const batch of chunk(paths, 10)) {
    await Promise.all(batch.map(async (p) => {
      const res = await api(cfg, `/repository/files/${encodeURIComponent(p)}/raw?ref=${encodeURIComponent(branch)}`);
      files[p] = await res.json();
    }));
  }
  return files;
}

// ---- Views (return HTML strings; every record field goes through escapeHtml) ----

const e = escapeHtml;
const pct = (x) => (typeof x === "number" ? x.toFixed(2) : "—"); // failed runs have no score
const summary = (run) => (run.status === "failed" ? "Automated review failed." : run.summary);

function link(cfg, netid, run) {
  const href = mrUrl(cfg.url, cfg.project, netid, run.mr_iid);
  return `<a href="${e(href)}" target="_blank" rel="noopener">MR !${e(run.mr_iid)}</a>`;
}

export function gridView(files) {
  const g = buildGrid(files);
  const head = g.assignments.map((a) => `<th>${e(a)}</th>`).join("");
  const rows = g.students.map((s) => {
    const tds = g.assignments.map((a) => {
      const c = g.cell(s.netid, a);
      if (!c) return `<td class="empty">-</td>`;
      const r = c.run;
      return `<td class="${c.flagged ? "flag" : ""}">${e(r.test_passed)}/${e(r.test_total)} tests<br>` +
        `score ${pct(r.recommended_score)}${c.flagged ? " &#9873;" : ""}</td>`;
    }).join("");
    return `<tr><th><a href="#/student/${e(s.netid)}">${e(s.netid)}</a></th>${tds}</tr>`;
  }).join("");
  return `<div class="scroll"><table><tr><th></th>${head}</tr>${rows}</table></div>`;
}

export function flaggedView(files, cfg) {
  const rows = [];
  for (const [path, records] of Object.entries(files)) {
    const p = parsePath(path);
    const run = p && latestRun(records);
    if (run && isFlagged(run)) rows.push({ ...p, netid: p.netid, run });
  }
  const items = urgencySort(rows).map(({ netid, assignment, run }) => {
    const why = run.integrity_flag ? "INTEGRITY" : run.needs_human ? "needs human"
      : run.status === "failed" ? "review failed" : "";
    return `<tr><td><a href="#/student/${e(netid)}">${e(netid)}</a></td><td>${e(assignment)}</td>` +
      `<td>${why}</td><td>${pct(run.recommended_score)}</td>` +
      `<td>${e(run.needs_human_reason || summary(run))}</td><td>${link(cfg, netid, run)}</td></tr>`;
  }).join("");
  return `<div class="scroll"><table><tr><th>student</th><th>assignment</th><th></th>` +
    `<th>score</th><th>why</th><th>MR</th></tr>${items || '<tr><td colspan="6">Nothing flagged.</td></tr>'}</table></div>`;
}

export function studentView(files, netid, cfg) {
  const blocks = Object.entries(files)
    .filter(([path]) => parsePath(path)?.netid === netid)
    .map(([path, records]) => {
      const trend = records.map((r) => pct(r.recommended_score)).join(" &rarr; ");
      const runs = records.map((r) =>
        `<li>${e(r.timestamp)}: ${e(r.test_passed)}/${e(r.test_total)} tests, ` +
        `score ${pct(r.recommended_score)} ${link(cfg, netid, r)}<br>${e(summary(r))}</li>`).join("");
      return `<h3>${e(parsePath(path).assignment)}</h3><p>Trend: ${trend}</p><ul>${runs}</ul>`;
    }).join("");
  return `<h2>${e(netid)}</h2>${blocks || "<p>No submissions.</p>"}`;
}

// ---- DOM bootstrap (skipped under node --test) ----

if (typeof document !== "undefined") {
  const KEY = "dashboard-config";
  const app = document.getElementById("app");
  let files = null;
  let renderSeq = 0; // a newer render() makes older, slower ones give up
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY)); } catch { return null; } };

  function showForm() {
    app.innerHTML = `<form id="setup"><h2>Connect</h2>
      <label>GitLab URL <input name="url" value="https://gitlab.oit.duke.edu" required></label>
      <label>Results project path <input name="project" placeholder="group/staff/results" required></label>
      <label>Read-only token <input name="token" type="password" required></label>
      <button>Save</button></form>`;
    document.getElementById("setup").onsubmit = (ev) => {
      ev.preventDefault();
      const f = Object.fromEntries(new FormData(ev.target));
      f.url = f.url.replace(/\/+$/, "");
      localStorage.setItem(KEY, JSON.stringify(f));
      files = null;
      render();
    };
  }

  async function render() {
    const seq = ++renderSeq;
    const cfg = load();
    document.getElementById("nav").hidden = !cfg;
    if (!cfg) return showForm();
    try {
      if (!files) { app.textContent = "Loading..."; files = await fetchResults(cfg); }
      if (seq !== renderSeq) return;
    } catch (err) {
      if (seq !== renderSeq) return;
      app.textContent = `Could not load results: ${err.message}`;
      return;
    }
    const hash = location.hash || "#/";
    const student = /^#\/student\/(.+)$/.exec(hash);
    app.innerHTML = hash === "#/flagged" ? flaggedView(files, cfg)
      : student ? studentView(files, decodeURIComponent(student[1]), cfg)
      : gridView(files);
  }

  document.getElementById("signout").onclick = () => {
    localStorage.removeItem(KEY);
    files = null;
    render();
  };
  window.addEventListener("hashchange", render);
  render();
}
