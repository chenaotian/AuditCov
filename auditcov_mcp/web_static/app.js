const STORAGE_KEY = "auditcov.webViewerState.v2";

const state = {
  projects: [],
  selectedProjectId: null,
  selectedSessionIds: new Set(),
  selectedFilePath: null,
  detail: null,
  coverage: null,
  settings: null,
  expandedTreePaths: new Set([""]),
};

const ids = [
  "refreshButton", "projectForm", "projectRootInput", "projectNameInput",
  "projectCreateButton", "projectMessage", "workdirForm", "workdirInput",
  "workdirButton", "settingsMeta", "settingsMessage", "projectList",
  "projectKicker", "projectTitle", "projectMeta", "metricGrid",
  "threadSelector", "treeView", "filePath", "fileStats", "codeView",
];
const els = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));

restoreState();
els.refreshButton.addEventListener("click", loadProjects);
els.projectForm.addEventListener("submit", createProject);
els.workdirForm.addEventListener("submit", moveWorkdir);
loadProjects().catch(showFatal);

async function createProject(event) {
  event.preventDefault();
  const project_root = els.projectRootInput.value.trim();
  const name = els.projectNameInput.value.trim();
  if (!project_root) return setMessage(els.projectMessage, "Repository root is required.", "error");
  els.projectCreateButton.disabled = true;
  setMessage(els.projectMessage, "Creating frozen source snapshot...", "");
  try {
    const project = await postJson("/api/projects", { project_root, name: name || null });
    state.selectedProjectId = project.id;
    state.selectedSessionIds = new Set();
    els.projectRootInput.value = "";
    els.projectNameInput.value = "";
    setMessage(els.projectMessage, `Created ${project.name}.`, "ok");
    await loadProjects();
  } catch (error) {
    setMessage(els.projectMessage, error.message, "error");
  } finally {
    els.projectCreateButton.disabled = false;
  }
}

async function loadProjects() {
  state.settings = await fetchJson("/api/settings");
  renderSettings();
  const data = await fetchJson("/api/projects");
  state.projects = data.projects || [];
  renderProjectList();
  if (!state.projects.length) return renderEmpty();
  if (!state.projects.some((item) => item.id === state.selectedProjectId)) {
    state.selectedProjectId = state.projects[0].id;
  }
  await loadProject(state.selectedProjectId);
}

async function loadProject(projectId) {
  const changed = projectId !== state.selectedProjectId;
  state.selectedProjectId = projectId;
  if (changed) {
    state.selectedFilePath = null;
    state.expandedTreePaths = new Set([""]);
  }
  state.detail = await fetchJson(`/api/projects/${projectId}`);
  const available = new Set(state.detail.sessions.map((session) => session.id));
  const retained = [...state.selectedSessionIds].filter((id) => available.has(id));
  state.selectedSessionIds = new Set(retained.length ? retained : available);
  renderProjectList();
  await loadCoverage();
}

async function loadCoverage() {
  const params = selectionParams();
  state.coverage = await fetchJson(
    `/api/projects/${state.selectedProjectId}/coverage?${params.toString()}`,
  );
  renderProject();
  saveState();
  if (state.selectedFilePath) {
    try { await loadFile(state.selectedFilePath); } catch (_error) { state.selectedFilePath = null; }
  }
}

async function loadFile(path) {
  const params = selectionParams();
  params.set("path", path);
  const file = await fetchJson(
    `/api/projects/${state.selectedProjectId}/file?${params.toString()}`,
  );
  state.selectedFilePath = path;
  expandParents(path);
  renderTree(state.coverage.tree);
  renderFile(file);
  saveState();
}

function selectionParams() {
  const params = new URLSearchParams();
  if (!state.selectedSessionIds.size) params.set("selection", "none");
  for (const id of state.selectedSessionIds) params.append("session_id", String(id));
  return params;
}

async function moveWorkdir(event) {
  event.preventDefault();
  const work_dir = els.workdirInput.value.trim();
  if (!work_dir) return setMessage(els.settingsMessage, "Work directory is required.", "error");
  els.workdirButton.disabled = true;
  try {
    state.settings = await postJson("/api/settings/workdir", { work_dir });
    renderSettings();
    setMessage(els.settingsMessage, "Work directory updated.", "ok");
    await loadProjects();
  } catch (error) {
    setMessage(els.settingsMessage, error.message, "error");
  } finally {
    els.workdirButton.disabled = !state.settings?.can_update_work_dir;
  }
}

function renderSettings() {
  if (!state.settings) return;
  els.workdirInput.value = state.settings.work_dir || "";
  els.workdirInput.disabled = !state.settings.can_update_work_dir;
  els.workdirButton.disabled = !state.settings.can_update_work_dir;
  els.settingsMeta.textContent = `DB ${state.settings.db_path}`;
  if (!state.settings.can_update_work_dir) {
    setMessage(els.settingsMessage, `Locked by ${state.settings.override_reason}.`, "error");
  }
}

function renderProjectList() {
  els.projectList.replaceChildren();
  if (!state.projects.length) {
    const node = document.createElement("div");
    node.className = "empty-state";
    node.textContent = "Create a repository project to begin tracking reads.";
    return els.projectList.appendChild(node);
  }
  for (const project of state.projects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `project-item${project.id === state.selectedProjectId ? " active" : ""}`;
    button.addEventListener("click", () => loadProject(project.id));
    button.innerHTML = `
      <div class="project-name"></div>
      <div class="project-thread"></div>
      <div class="project-root"></div>
      <div class="mini-bar"><div class="mini-bar-fill"></div></div>`;
    button.querySelector(".project-name").textContent = project.name;
    button.querySelector(".project-thread").textContent =
      `${project.session_count} sessions | ${formatPercent(project.percent)}`;
    button.querySelector(".project-root").textContent = project.project_root;
    button.querySelector(".mini-bar-fill").style.width = `${clamp(project.percent)}%`;
    els.projectList.appendChild(button);
  }
}

function renderProject() {
  const detail = state.detail;
  const coverage = state.coverage;
  els.projectKicker.textContent = `${detail.session_count} agent sessions`;
  els.projectTitle.textContent = detail.name;
  els.projectMeta.textContent = detail.project_root;
  renderMetrics(detail, coverage);
  renderSessionSelector(detail.sessions);
  renderTree(coverage.tree);
  if (!state.selectedFilePath) {
    els.filePath.textContent = "Select a file from the project snapshot.";
    els.fileStats.textContent = "";
    els.codeView.className = "code-view empty-state";
    els.codeView.textContent = "Covered lines will appear with a solid left rail.";
  }
}

function renderMetrics(detail, coverage) {
  const metrics = [
    ["Selected coverage", formatPercent(coverage.percent), `${coverage.covered_lines} / ${coverage.total_lines} lines`],
    ["All sessions", formatPercent(detail.percent), `${detail.covered_lines} / ${detail.total_lines} lines`],
    ["Selected sessions", String(state.selectedSessionIds.size), `${detail.session_count} available`],
    ["Frozen files", String(detail.total_files), `${detail.total_lines} source lines`],
  ];
  els.metricGrid.replaceChildren();
  for (const [label, value, sub] of metrics) {
    const node = document.createElement("div");
    node.className = "metric";
    node.innerHTML = '<div class="metric-label"></div><div class="metric-value"></div><div class="metric-sub"></div>';
    node.children[0].textContent = label;
    node.children[1].textContent = value;
    node.children[2].textContent = sub;
    els.metricGrid.appendChild(node);
  }
}

function renderSessionSelector(sessions) {
  els.threadSelector.className = "thread-selector";
  els.threadSelector.replaceChildren();
  const header = document.createElement("div");
  header.className = "thread-selector-header";
  header.innerHTML = '<div class="panel-title">Agent sessions</div><div class="thread-actions"></div>';
  for (const [text, action] of [
    ["All", () => { state.selectedSessionIds = new Set(sessions.map((item) => item.id)); loadCoverage(); }],
    ["None", () => { state.selectedSessionIds = new Set(); loadCoverage(); }],
  ]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "small-button";
    button.textContent = text;
    button.addEventListener("click", action);
    header.children[1].appendChild(button);
  }
  els.threadSelector.appendChild(header);
  const list = document.createElement("div");
  list.className = "thread-list";
  if (!sessions.length) {
    list.className += " empty-state";
    list.textContent = "No tracked Read calls yet.";
  }
  for (const session of sessions) {
    const label = document.createElement("label");
    label.className = `thread-row${state.selectedSessionIds.has(session.id) ? " selected" : ""}`;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedSessionIds.has(session.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedSessionIds.add(session.id);
      else state.selectedSessionIds.delete(session.id);
      loadCoverage();
    });
    const main = document.createElement("div");
    main.className = "thread-main";
    const title = document.createElement("div");
    title.className = "thread-id";
    title.textContent = `${session.agent_type} · ${shortId(session.agent_session_id)}`;
    title.title = session.agent_session_id;
    const sub = document.createElement("div");
    sub.className = "thread-targets";
    sub.textContent = `${formatPercent(session.percent)} | ${session.covered_lines}/${session.total_lines}`;
    main.append(title, sub);
    label.append(checkbox, main);
    list.appendChild(label);
  }
  els.threadSelector.appendChild(list);
}

function renderTree(root) {
  els.treeView.className = "tree-view";
  els.treeView.replaceChildren(renderTreeNode(root));
}

function renderTreeNode(node) {
  const wrapper = document.createElement("div");
  const row = document.createElement("button");
  const key = node.path || "";
  const directory = node.type !== "file";
  const expanded = directory && state.expandedTreePaths.has(key);
  row.type = "button";
  row.className = `tree-row${node.path === state.selectedFilePath ? " active" : ""}`;
  row.innerHTML = '<div class="tree-marker"></div><div class="tree-kind"></div><div class="tree-name"></div><div class="tree-percent"></div>';
  row.children[0].textContent = directory ? (expanded ? "-" : "+") : "";
  row.children[1].textContent = directory ? "DIR" : "FILE";
  row.children[2].textContent = node.name;
  row.children[3].textContent = formatPercent(node.percent);
  wrapper.appendChild(row);
  if (!directory) {
    row.addEventListener("click", () => loadFile(node.path));
    return wrapper;
  }
  const children = document.createElement("div");
  children.className = `tree-children${expanded ? "" : " collapsed"}`;
  for (const child of node.children || []) children.appendChild(renderTreeNode(child));
  row.addEventListener("click", () => {
    if (expanded) state.expandedTreePaths.delete(key); else state.expandedTreePaths.add(key);
    renderTree(state.coverage.tree);
    saveState();
  });
  wrapper.appendChild(children);
  return wrapper;
}

function renderFile(file) {
  els.filePath.textContent = file.path;
  els.fileStats.textContent = `${formatPercent(file.percent)} | ${file.covered_lines} / ${file.total_lines} lines`;
  if (file.content_changed) els.fileStats.textContent += " | snapshot changed";
  els.codeView.className = "code-view";
  els.codeView.replaceChildren();
  const fragment = document.createDocumentFragment();
  for (const line of file.lines) {
    const row = document.createElement("div");
    row.className = `code-line ${line.covered ? "covered" : "uncovered"}`;
    const number = document.createElement("div");
    number.className = "line-number";
    number.textContent = line.number;
    const rail = document.createElement("div");
    rail.className = "line-rail";
    const code = document.createElement("div");
    code.className = "line-code";
    code.textContent = line.text || " ";
    row.append(number, rail, code);
    fragment.appendChild(row);
  }
  els.codeView.appendChild(fragment);
}

function renderEmpty() {
  state.detail = null;
  state.coverage = null;
  els.projectKicker.textContent = "No project selected";
  els.projectTitle.textContent = "Audit coverage viewer";
  els.projectMeta.textContent = "";
  els.metricGrid.replaceChildren();
  els.threadSelector.className = "thread-selector empty-state";
  els.threadSelector.textContent = "Create a repository project first.";
  els.treeView.className = "tree-view empty-state";
  els.treeView.textContent = "No project snapshots found.";
  els.codeView.className = "code-view empty-state";
  els.codeView.textContent = "Tracked source coverage will appear here.";
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}

function setMessage(element, text, kind) {
  element.textContent = text;
  element.className = `settings-message${kind ? ` ${kind}` : ""}`;
}
function showFatal(error) { setMessage(els.projectMessage, error.message, "error"); }
function formatPercent(value) { return `${Number(value || 0).toFixed(2)}%`; }
function clamp(value) { return Math.max(0, Math.min(100, Number(value || 0))); }
function shortId(value) { return value.length > 22 ? `${value.slice(0, 10)}...${value.slice(-7)}` : value; }
function expandParents(path) {
  state.expandedTreePaths.add("");
  const parts = path.split("/");
  for (let index = 1; index < parts.length; index += 1) {
    state.expandedTreePaths.add(parts.slice(0, index).join("/"));
  }
}
function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    selectedProjectId: state.selectedProjectId,
    selectedSessionIds: [...state.selectedSessionIds],
    selectedFilePath: state.selectedFilePath,
    expandedTreePaths: [...state.expandedTreePaths],
  }));
}
function restoreState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    if (Number.isInteger(saved.selectedProjectId)) state.selectedProjectId = saved.selectedProjectId;
    if (Array.isArray(saved.selectedSessionIds)) state.selectedSessionIds = new Set(saved.selectedSessionIds);
    if (typeof saved.selectedFilePath === "string") state.selectedFilePath = saved.selectedFilePath;
    if (Array.isArray(saved.expandedTreePaths)) state.expandedTreePaths = new Set(saved.expandedTreePaths);
  } catch (_error) { return; }
}
