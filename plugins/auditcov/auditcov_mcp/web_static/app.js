const state = {
  projects: [],
  selectedProjectRoot: null,
  selectedThreadIds: new Set(),
  selectedFilePath: null,
  rootDetail: null,
  project: null,
  settings: null,
};

const els = {
  refreshButton: document.getElementById("refreshButton"),
  workdirForm: document.getElementById("workdirForm"),
  workdirInput: document.getElementById("workdirInput"),
  workdirButton: document.getElementById("workdirButton"),
  settingsMeta: document.getElementById("settingsMeta"),
  settingsMessage: document.getElementById("settingsMessage"),
  projectList: document.getElementById("projectList"),
  projectKicker: document.getElementById("projectKicker"),
  projectTitle: document.getElementById("projectTitle"),
  projectMeta: document.getElementById("projectMeta"),
  metricGrid: document.getElementById("metricGrid"),
  threadSelector: document.getElementById("threadSelector"),
  treeView: document.getElementById("treeView"),
  filePath: document.getElementById("filePath"),
  fileStats: document.getElementById("fileStats"),
  codeView: document.getElementById("codeView"),
};

els.refreshButton.addEventListener("click", () => loadProjects());
els.workdirForm.addEventListener("submit", (event) => {
  event.preventDefault();
  moveWorkdir();
});

loadProjects();

async function loadProjects() {
  await loadSettings();
  const data = await fetchJson("/api/projects");
  state.projects = data.projects || [];
  renderProjectList();

  if (!state.projects.length) {
    renderEmpty();
    return;
  }

  const selectedExists = state.projects.some(
    (project) => project.project_root === state.selectedProjectRoot,
  );
  const nextRoot = selectedExists ? state.selectedProjectRoot : state.projects[0].project_root;
  await loadProjectRoot(nextRoot);
}

async function loadSettings() {
  state.settings = await fetchJson("/api/settings");
  renderSettings();
}

async function moveWorkdir() {
  const workDir = els.workdirInput.value.trim();
  if (!workDir) {
    setSettingsMessage("Work directory cannot be empty.", "error");
    return;
  }

  els.workdirButton.disabled = true;
  setSettingsMessage("Moving current AuditCov state...", "");
  try {
    state.settings = await postJson("/api/settings/workdir", { work_dir: workDir });
    renderSettings();
    setSettingsMessage(state.settings.moved ? "Work directory moved." : "Work directory unchanged.", "ok");
    await loadProjects();
  } catch (error) {
    setSettingsMessage(error.message || "Work directory cannot be changed right now.", "error");
  } finally {
    els.workdirButton.disabled = !state.settings?.can_update_work_dir;
  }
}

async function loadProjectRoot(projectRoot) {
  state.selectedProjectRoot = projectRoot;
  state.selectedFilePath = null;
  state.rootDetail = await fetchJson(
    `/api/projects/root?project_root=${encodeURIComponent(projectRoot)}`,
  );

  const available = new Set(state.rootDetail.threads.map((thread) => thread.thread_id));
  const retained = [...state.selectedThreadIds].filter((threadId) => available.has(threadId));
  state.selectedThreadIds = new Set(retained.length ? retained : [...available]);

  renderProjectList();
  await loadSelectedCoverage();
}

async function loadSelectedCoverage() {
  state.selectedFilePath = null;
  if (!state.selectedProjectRoot || state.selectedThreadIds.size === 0) {
    state.project = null;
    renderProject();
    return;
  }

  const params = selectedThreadParams();
  state.project = await fetchJson(`/api/projects/coverage?${params.toString()}`);
  renderProjectList();
  renderProject();
}

async function loadFile(path) {
  if (!state.selectedProjectRoot || state.selectedThreadIds.size === 0) {
    return;
  }
  state.selectedFilePath = path;
  const params = selectedThreadParams();
  params.set("path", path);
  const file = await fetchJson(`/api/projects/file?${params.toString()}`);
  renderTree(state.project.tree);
  renderFile(file);
}

function selectedThreadParams() {
  const params = new URLSearchParams();
  params.set("project_root", state.selectedProjectRoot);
  for (const threadId of state.selectedThreadIds) {
    params.append("thread_id", threadId);
  }
  return params;
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const data = await response.json();
  if (!response.ok || data.error) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || data.error) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

function renderSettings() {
  const settings = state.settings;
  if (!settings) {
    return;
  }

  els.workdirInput.value = settings.work_dir || "";
  els.workdirInput.disabled = !settings.can_update_work_dir;
  els.workdirButton.disabled = !settings.can_update_work_dir;
  els.settingsMeta.textContent = `DB ${settings.db_path}`;
  if (!settings.can_update_work_dir) {
    setSettingsMessage(`Locked by ${settings.override_reason}.`, "error");
  } else if (!els.settingsMessage.textContent) {
    setSettingsMessage("Default is the install directory workspace.", "");
  }
}

function setSettingsMessage(message, kind) {
  els.settingsMessage.textContent = message;
  els.settingsMessage.className = `settings-message${kind ? ` ${kind}` : ""}`;
}

function renderProjectList() {
  els.projectList.replaceChildren();
  if (!state.projects.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No project roots have called auditcov_init_project.";
    els.projectList.appendChild(empty);
    return;
  }

  for (const project of state.projects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `project-item${project.project_root === state.selectedProjectRoot ? " active" : ""}`;
    button.addEventListener("click", () => loadProjectRoot(project.project_root));

    const name = document.createElement("div");
    name.className = "project-name";
    name.textContent = project.project_label;
    name.title = project.project_root;

    const thread = document.createElement("div");
    thread.className = "project-thread";
    thread.textContent = `${project.thread_count} thread${project.thread_count === 1 ? "" : "s"} | ${formatPercent(project.percent)}`;

    const rootPath = document.createElement("div");
    rootPath.className = "project-root";
    rootPath.textContent = project.project_root;

    const bar = document.createElement("div");
    bar.className = "mini-bar";
    const fill = document.createElement("div");
    fill.className = "mini-bar-fill";
    fill.style.width = `${clampPercent(project.percent)}%`;
    bar.appendChild(fill);

    button.append(name, thread, rootPath, bar);
    els.projectList.appendChild(button);
  }
}

function renderEmpty() {
  els.projectKicker.textContent = "No project selected";
  els.projectTitle.textContent = "Audit coverage viewer";
  els.projectMeta.textContent = "";
  els.metricGrid.replaceChildren();
  els.threadSelector.className = "thread-selector empty-state";
  els.threadSelector.textContent = "Select a project root to choose sessions.";
  els.treeView.className = "tree-view empty-state";
  els.treeView.textContent = "No initialized projects found.";
  els.filePath.textContent = "Select a file from the target tree.";
  els.fileStats.textContent = "";
  els.codeView.className = "code-view empty-state";
  els.codeView.textContent = "Covered lines will appear with a solid left rail.";
}

function renderProject() {
  const detail = state.rootDetail;
  if (!detail) {
    renderEmpty();
    return;
  }

  els.projectKicker.textContent = `${detail.thread_count} thread${detail.thread_count === 1 ? "" : "s"} under root`;
  els.projectTitle.textContent = detail.project_label;
  els.projectMeta.textContent = detail.project_root;
  renderMetrics();
  renderThreadSelector();

  if (!state.project) {
    els.treeView.className = "tree-view empty-state";
    els.treeView.textContent = "Select at least one thread_id to view coverage.";
    els.filePath.textContent = "Select a file from the target tree.";
    els.fileStats.textContent = "";
    els.codeView.className = "code-view empty-state";
    els.codeView.textContent = "No selected session coverage.";
    return;
  }

  renderTree(state.project.tree);
  els.filePath.textContent = "Select a file from the target tree.";
  els.fileStats.textContent = "";
  els.codeView.className = "code-view empty-state";
  els.codeView.textContent = "Covered lines will appear with a solid left rail.";
}

function renderMetrics() {
  els.metricGrid.replaceChildren();
  const detail = state.rootDetail;
  const selected = state.project;
  const metrics = [
    [
      "Selected coverage",
      selected ? formatPercent(selected.percent) : "0.00%",
      selected
        ? `${selected.covered_lines} / ${selected.total_lines} lines`
        : "No selected threads",
    ],
    [
      "Root total",
      formatPercent(detail.percent),
      `${detail.covered_lines} / ${detail.total_lines} lines across all threads`,
    ],
    [
      "Selected threads",
      `${state.selectedThreadIds.size}`,
      `${detail.thread_count} available under this root`,
    ],
    [
      "Target paths",
      selected ? `${selected.target_paths.length}` : "0",
      selected?.target_paths?.join(", ") || "Select sessions to inspect scope",
    ],
  ];

  for (const [label, value, sub] of metrics) {
    const node = document.createElement("div");
    node.className = "metric";

    const labelNode = document.createElement("div");
    labelNode.className = "metric-label";
    labelNode.textContent = label;

    const valueNode = document.createElement("div");
    valueNode.className = "metric-value";
    valueNode.textContent = value;

    const subNode = document.createElement("div");
    subNode.className = "metric-sub";
    subNode.textContent = sub;

    node.append(labelNode, valueNode, subNode);
    els.metricGrid.appendChild(node);
  }
}

function renderThreadSelector() {
  const detail = state.rootDetail;
  els.threadSelector.className = "thread-selector";
  els.threadSelector.replaceChildren();

  const header = document.createElement("div");
  header.className = "thread-selector-header";

  const title = document.createElement("div");
  title.className = "panel-title";
  title.textContent = "Sessions";

  const actions = document.createElement("div");
  actions.className = "thread-actions";

  const allButton = document.createElement("button");
  allButton.type = "button";
  allButton.className = "small-button";
  allButton.textContent = "All";
  allButton.addEventListener("click", () => {
    state.selectedThreadIds = new Set(detail.threads.map((thread) => thread.thread_id));
    loadSelectedCoverage();
  });

  const noneButton = document.createElement("button");
  noneButton.type = "button";
  noneButton.className = "small-button";
  noneButton.textContent = "None";
  noneButton.addEventListener("click", () => {
    state.selectedThreadIds = new Set();
    loadSelectedCoverage();
  });

  actions.append(allButton, noneButton);
  header.append(title, actions);
  els.threadSelector.appendChild(header);

  const list = document.createElement("div");
  list.className = "thread-list";
  for (const thread of detail.threads) {
    const label = document.createElement("label");
    label.className = `thread-row${state.selectedThreadIds.has(thread.thread_id) ? " selected" : ""}`;

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedThreadIds.has(thread.thread_id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.selectedThreadIds.add(thread.thread_id);
      } else {
        state.selectedThreadIds.delete(thread.thread_id);
      }
      loadSelectedCoverage();
    });

    const main = document.createElement("div");
    main.className = "thread-main";

    const id = document.createElement("div");
    id.className = "thread-id";
    id.textContent = shortId(thread.thread_id);
    id.title = thread.thread_id;

    const targets = document.createElement("div");
    targets.className = "thread-targets";
    targets.textContent = thread.target_paths.join(", ");

    main.append(id, targets);

    const coverage = document.createElement("div");
    coverage.className = "thread-coverage";
    coverage.textContent = `${formatPercent(thread.percent)} | ${thread.covered_lines}/${thread.total_lines}`;

    label.append(checkbox, main, coverage);
    list.appendChild(label);
  }

  els.threadSelector.appendChild(list);
}

function renderTree(root) {
  els.treeView.className = "tree-view";
  els.treeView.replaceChildren();
  els.treeView.appendChild(renderTreeNode(root, 0));
}

function renderTreeNode(node, depth) {
  const wrapper = document.createElement("div");
  const row = document.createElement("button");
  row.type = "button";
  row.className = `tree-row${node.path === state.selectedFilePath ? " active" : ""}`;

  const kind = document.createElement("div");
  kind.className = "tree-kind";
  kind.textContent = node.type === "file" ? "FILE" : "DIR";

  const name = document.createElement("div");
  name.className = "tree-name";
  name.textContent = depth === 0 ? node.name : node.name;
  name.title = node.path || node.name;

  const pct = document.createElement("div");
  pct.className = "tree-percent";
  pct.textContent = formatPercent(node.percent);

  row.append(kind, name, pct);
  wrapper.appendChild(row);

  if (node.type === "file") {
    row.addEventListener("click", () => loadFile(node.path));
    return wrapper;
  }

  const children = document.createElement("div");
  children.className = "tree-children";
  for (const child of node.children || []) {
    children.appendChild(renderTreeNode(child, depth + 1));
  }

  row.addEventListener("click", () => {
    children.classList.toggle("collapsed");
  });
  wrapper.appendChild(children);
  return wrapper;
}

function renderFile(file) {
  els.filePath.textContent = file.path;
  els.fileStats.textContent = `${formatPercent(file.percent)} | ${file.covered_lines} / ${file.total_lines} lines`;
  if (file.content_changed) {
    const warning = document.createElement("span");
    warning.className = "warning";
    warning.textContent = " | snapshot changed";
    els.fileStats.appendChild(warning);
  }

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

function shortId(value) {
  if (!value) {
    return "";
  }
  return value.length > 18 ? `${value.slice(0, 9)}...${value.slice(-6)}` : value;
}

function formatPercent(value) {
  return `${Number(value || 0).toFixed(2)}%`;
}

function clampPercent(value) {
  const number = Number(value || 0);
  return Math.max(0, Math.min(100, number));
}
