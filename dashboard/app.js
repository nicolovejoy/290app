// Instructor dashboard: reads review results from the staff/results GitLab project.
// Pure logic first (tested by app.test.mjs), thin DOM code at the bottom.

// Keep in sync with tooling/review/flag.py
const MIN_SCORE = 0.6;
const MAX_DISAGREEMENT = 0.2;

// Mirrors should_flag() in tooling/review/flag.py, boundaries included.
export function isFlagged(run) {
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

// rows: [{netid, run, ...}]. Integrity first, then needs_human, then lowest score.
export function urgencySort(rows) {
  const rank = (r) => (r.run.integrity_flag ? 0 : r.run.needs_human ? 1 : 2);
  return [...rows].sort(
    (a, b) => rank(a) - rank(b) || a.run.recommended_score - b.run.recommended_score);
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
    for (const e of await res.json()) {
      if (e.type === "blob" && parsePath(e.path)) paths.push(e.path);
    }
    page = res.headers.get("x-next-page"); // empty on the last page
  }
  return paths;
}

export async function fetchResults(cfg) {
  const files = {};
  const paths = await listJsonPaths(cfg);
  await Promise.all(paths.map(async (p) => {
    const res = await api(cfg, `/repository/files/${encodeURIComponent(p)}/raw?ref=main`);
    files[p] = await res.json();
  }));
  return files;
}

// ---- Views (return HTML strings; every record field goes through escapeHtml) ----

const e = escapeHtml;
const pct = (x) => x.toFixed(2);

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
    const why = run.integrity_flag ? "INTEGRITY" : run.needs_human ? "needs human" : "";
    return `<tr><td><a href="#/student/${e(netid)}">${e(netid)}</a></td><td>${e(assignment)}</td>` +
      `<td>${why}</td><td>${pct(run.recommended_score)}</td>` +
      `<td>${e(run.needs_human_reason || run.summary)}</td><td>${link(cfg, netid, run)}</td></tr>`;
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
        `score ${pct(r.recommended_score)} ${link(cfg, netid, r)}<br>${e(r.summary)}</li>`).join("");
      return `<h3>${e(parsePath(path).assignment)}</h3><p>Trend: ${trend}</p><ul>${runs}</ul>`;
    }).join("");
  return `<h2>${e(netid)}</h2>${blocks || "<p>No submissions.</p>"}`;
}

// ---- DOM bootstrap (skipped under node --test) ----

if (typeof document !== "undefined") {
  const KEY = "dashboard-config";
  const app = document.getElementById("app");
  let files = null;
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
    const cfg = load();
    document.getElementById("nav").hidden = !cfg;
    if (!cfg) return showForm();
    try {
      if (!files) { app.textContent = "Loading..."; files = await fetchResults(cfg); }
    } catch (err) {
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
