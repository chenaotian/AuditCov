const state = {
  projects: [],
  selectedThreadId: null,
  selectedFilePath: null,
  project: null,
};

const els = {
  refreshButton: document.getElementById("refreshButton"),
  projectList: document.getElementById("projectList"),
  projectKicker: document.getElementById("projectKicker"),
  projectTitle: document.getElementById("projectTitle"),
  projectMeta: document.getElementById("projectMeta"),
  metricGrid: document.getElementById("metricGrid"),
  treeView: document.getElementById("treeView"),
  filePath: document.getElementById("filePath"),
  fileStats: document.getElementById("fileStats"),
  codeView: document.getElementById("codeView"),
};

els.refreshButton.addEventListener("click", () => loadProjects());

loadProjects();

async function loadProjects() {
  const data = await fetchJson("/api/projects");
  state.projects = data.projects || [];
  renderProjectList();

  if (!state.projects.length) {
    renderEmpty();
    return;
  }

  const selectedExists = state.projects.some((project) => project.thread_id === state.selectedThreadId);
  const nextThreadId = selectedExists ? state.selectedThreadId : state.projects[0].thread_id;
  await loadProject(nextThreadId);
}

async function loadProject(threadId) {
  state.selectedThreadId = threadId;
  state.selectedFilePath = null;
  state.project = await fetchJson(`/api/projects/${encodeURIComponent(threadId)}`);
  renderProjectList();
  renderProject();
}

async function loadFile(path) {
  state.selectedFilePath = path;
  const encodedThread = encodeURIComponent(state.selectedThreadId);
  const encodedPath = encodeURIComponent(path);
  const file = await fetchJson(`/api/projects/${encodedThread}/file?path=${encodedPath}`);
  renderTree(state.project.tree);
  renderFile(file);
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const data = await response.json();
  if (!response.ok || data.error) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

function renderProjectList() {
  els.projectList.replaceChildren();
  if (!state.projects.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No projects have called auditcov_init_project.";
    els.projectList.appendChild(empty);
    return;
  }

  for (const project of state.projects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `project-item${project.thread_id === state.selectedThreadId ? " active" : ""}`;
    button.addEventListener("click", () => loadProject(project.thread_id));

    const name = document.createElement("div");
    name.className = "project-name";
    name.textContent = project.project_label;

    const thread = document.createElement("div");
    thread.className = "project-thread";
    thread.textContent = shortId(project.thread_id);

    const bar = document.createElement("div");
    bar.className = "mini-bar";
    const fill = document.createElement("div");
    fill.className = "mini-bar-fill";
    fill.style.width = `${clampPercent(project.percent)}%`;
    bar.appendChild(fill);

    button.append(name, thread, bar);
    els.projectList.appendChild(button);
  }
}

function renderEmpty() {
  els.projectKicker.textContent = "No project selected";
  els.projectTitle.textContent = "Audit coverage viewer";
  els.projectMeta.textContent = "";
  els.metricGrid.replaceChildren();
  els.treeView.className = "tree-view empty-state";
  els.treeView.textContent = "No initialized projects found.";
  els.filePath.textContent = "Select a file from the target tree.";
  els.fileStats.textContent = "";
  els.codeView.className = "code-view empty-state";
  els.codeView.textContent = "Covered lines will appear with a solid left rail.";
}

function renderProject() {
  const project = state.project;
  els.projectKicker.textContent = shortId(project.thread_id);
  els.projectTitle.textContent = project.project_label;
  els.projectMeta.textContent = project.project_root;
  renderMetrics(project);
  renderTree(project.tree);
  els.filePath.textContent = "Select a file from the target tree.";
  els.fileStats.textContent = "";
  els.codeView.className = "code-view empty-state";
  els.codeView.textContent = "Covered lines will appear with a solid left rail.";
}

function renderMetrics(project) {
  els.metricGrid.replaceChildren();
  const metrics = [
    ["Read coverage", formatPercent(project.percent), `${project.covered_lines} / ${project.total_lines} lines`],
    ["Covered files", `${project.covered_files}`, `${project.total_files} target files`],
    ["Target paths", `${project.target_paths.length}`, project.target_paths.join(", ")],
    ["Response cap", `${project.max_response_bytes} B`, "complete-line truncation"],
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
