const STORAGE_KEY = "auditcov.webViewerState.v2";
const WORK_AREA_MIN_LEFT_WIDTH = 260;
const WORK_AREA_MIN_CODE_WIDTH = 320;
const WORK_AREA_RESIZE_STEP = 24;
const WORK_AREA_DESKTOP_QUERY = "(min-width: 981px)";

const state = {
  projects: [],
  selectedProjectId: null,
  selectedSessionIds: new Set(),
  selectedFilePath: null,
  detail: null,
  coverage: null,
  settings: null,
  leftColumnWidth: null,
  fileViewMode: "tree",
  fileSortDirection: "desc",
  expandedTreePaths: new Set([""]),
  expandedSessionIds: new Set(),
};

const ids = [
  "refreshButton", "projectForm", "projectRootInput", "projectNameInput",
  "projectCreateButton", "projectMessage", "workdirForm", "workdirInput",
  "workdirButton", "settingsMeta", "settingsMessage", "projectList",
  "projectKicker", "projectTitle", "projectMeta", "metricGrid",
  "threadSelector", "directoryViewButton", "allFilesViewButton",
  "fileSortSelect", "treeView", "filePath", "fileStats", "codeView",
  "workArea", "leftColumn", "workAreaResizer",
];
const els = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));

restoreState();
setupWorkAreaResizer();
setupFileNavigationControls();
els.refreshButton.addEventListener("click", loadProjects);
els.projectForm.addEventListener("submit", createProject);
els.workdirForm.addEventListener("submit", moveWorkdir);
loadProjects().catch(showFatal);

function setupWorkAreaResizer() {
  const resizer = els.workAreaResizer;
  let dragging = false;
  let dragStartX = 0;
  let dragStartWidth = 0;

  const resizeFromPointer = (event) => {
    if (!dragging) return;
    setLeftColumnWidth(dragStartWidth + event.clientX - dragStartX);
  };

  const stopDragging = (event) => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("resizing-work-area");
    if (resizer.hasPointerCapture(event.pointerId)) {
      resizer.releasePointerCapture(event.pointerId);
    }
    saveState();
  };

  resizer.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !event.isPrimary || dragging || !workAreaIsResizable()) return;
    event.preventDefault();
    dragging = true;
    dragStartX = event.clientX;
    dragStartWidth = els.leftColumn.getBoundingClientRect().width;
    document.body.classList.add("resizing-work-area");
    resizer.setPointerCapture(event.pointerId);
  });
  resizer.addEventListener("pointermove", resizeFromPointer);
  resizer.addEventListener("pointerup", stopDragging);
  resizer.addEventListener("pointercancel", stopDragging);
  resizer.addEventListener("lostpointercapture", stopDragging);
  resizer.addEventListener("keydown", resizeWorkAreaFromKeyboard);
  window.addEventListener("resize", applyWorkAreaWidth);

  applyWorkAreaWidth();
}

function workAreaIsResizable() {
  return window.matchMedia(WORK_AREA_DESKTOP_QUERY).matches;
}

function applyWorkAreaWidth() {
  if (!workAreaIsResizable()) return;
  if (state.leftColumnWidth === null) {
    els.workArea.style.removeProperty("--left-column-width");
  }
  setLeftColumnWidth(state.leftColumnWidth ?? renderedLeftColumnWidth(), false);
}

function workAreaWidthBounds() {
  const totalWidth = els.workArea.getBoundingClientRect().width;
  const resizerWidth = els.workAreaResizer.getBoundingClientRect().width || 14;
  return {
    min: WORK_AREA_MIN_LEFT_WIDTH,
    max: Math.max(
      WORK_AREA_MIN_LEFT_WIDTH,
      Math.floor(totalWidth - resizerWidth - WORK_AREA_MIN_CODE_WIDTH),
    ),
  };
}

function renderedLeftColumnWidth() {
  return els.leftColumn.getBoundingClientRect().width;
}

function setLeftColumnWidth(value, rememberPreference = true) {
  const bounds = workAreaWidthBounds();
  const numericValue = Number(value);
  const requested = Number.isFinite(numericValue) ? numericValue : WORK_AREA_MIN_LEFT_WIDTH;
  const width = Math.round(Math.max(bounds.min, Math.min(bounds.max, requested)));
  if (rememberPreference) state.leftColumnWidth = width;
  els.workArea.style.setProperty("--left-column-width", `${width}px`);
  els.workAreaResizer.setAttribute("aria-valuemin", String(bounds.min));
  els.workAreaResizer.setAttribute("aria-valuemax", String(bounds.max));
  els.workAreaResizer.setAttribute("aria-valuenow", String(width));
  return width;
}

function resizeWorkAreaFromKeyboard(event) {
  if (!workAreaIsResizable()) return;
  const bounds = workAreaWidthBounds();
  const current = renderedLeftColumnWidth();
  let next;
  if (event.key === "ArrowLeft") next = current - WORK_AREA_RESIZE_STEP;
  else if (event.key === "ArrowRight") next = current + WORK_AREA_RESIZE_STEP;
  else if (event.key === "Home") next = bounds.min;
  else if (event.key === "End") next = bounds.max;
  else return;
  event.preventDefault();
  setLeftColumnWidth(next);
  saveState();
}

function setupFileNavigationControls() {
  els.directoryViewButton.addEventListener("click", () => setFileViewMode("tree"));
  els.allFilesViewButton.addEventListener("click", () => setFileViewMode("files"));
  els.fileSortSelect.addEventListener("change", () => {
    state.fileSortDirection = els.fileSortSelect.value === "asc" ? "asc" : "desc";
    if (state.coverage && state.fileViewMode === "files") renderTree(state.coverage.tree);
    saveState();
  });
  updateFileNavigationControls();
}

function setFileViewMode(mode) {
  state.fileViewMode = mode === "files" ? "files" : "tree";
  updateFileNavigationControls();
  if (state.coverage) renderTree(state.coverage.tree);
  saveState();
}

function updateFileNavigationControls() {
  const directoryMode = state.fileViewMode !== "files";
  els.directoryViewButton.classList.toggle("active", directoryMode);
  els.directoryViewButton.setAttribute("aria-pressed", String(directoryMode));
  els.allFilesViewButton.classList.toggle("active", !directoryMode);
  els.allFilesViewButton.setAttribute("aria-pressed", String(!directoryMode));
  els.fileSortSelect.value = state.fileSortDirection === "asc" ? "asc" : "desc";
  els.fileSortSelect.hidden = directoryMode;
  els.fileSortSelect.disabled = directoryMode;
}

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
    state.expandedSessionIds = new Set();
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
    const item = document.createElement("div");
    item.className = `project-item${project.id === state.selectedProjectId ? " active" : ""}`;
    const selectButton = document.createElement("button");
    selectButton.type = "button";
    selectButton.className = "project-select";
    selectButton.addEventListener("click", () => loadProject(project.id));
    selectButton.innerHTML = `
      <div class="project-name"></div>
      <div class="project-thread"></div>
      <div class="project-root"></div>
      <div class="mini-bar"><div class="mini-bar-fill"></div></div>`;
    selectButton.querySelector(".project-name").textContent = project.name;
    selectButton.querySelector(".project-thread").textContent =
      `${project.session_count} sessions | ${formatPercent(project.percent)}`;
    selectButton.querySelector(".project-root").textContent = project.project_root;
    selectButton.querySelector(".mini-bar-fill").style.width = `${clamp(project.percent)}%`;

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "project-delete";
    deleteButton.textContent = "Delete";
    deleteButton.title = `Delete project ${project.name}`;
    deleteButton.setAttribute("aria-label", `Delete project ${project.name}`);
    deleteButton.addEventListener("click", () => deleteProject(project, deleteButton));

    item.append(selectButton, deleteButton);
    els.projectList.appendChild(item);
  }
}

async function deleteProject(project, deleteButton) {
  const confirmed = window.confirm(
    `Delete AuditCov project "${project.name}"?\n\n` +
    `This permanently deletes its snapshot, sessions, read events, and coverage records ` +
    `from the local AuditCov database.\n\n` +
    `Repository files at ${project.project_root} will not be deleted. ` +
    `This action cannot be undone.`,
  );
  if (!confirmed) return;

  deleteButton.disabled = true;
  deleteButton.textContent = "Deleting...";
  try {
    await deleteJson(`/api/projects/${project.id}`);
  } catch (error) {
    deleteButton.disabled = false;
    deleteButton.textContent = "Delete";
    setMessage(els.projectMessage, error.message, "error");
    return;
  }

  const deletedSelectedProject = project.id === state.selectedProjectId;
  state.projects = state.projects.filter((item) => item.id !== project.id);
  if (deletedSelectedProject) clearProjectSelection();
  renderProjectList();
  if (deletedSelectedProject || !state.projects.length) renderEmpty();
  saveState();
  setMessage(els.projectMessage, `Deleted ${project.name}.`, "ok");
  try {
    await loadProjects();
  } catch (error) {
    setMessage(
      els.projectMessage,
      `Deleted ${project.name}, but refreshing the project list failed: ${error.message}`,
      "error",
    );
  }
}

function clearProjectSelection() {
  state.selectedProjectId = null;
  state.selectedSessionIds = new Set();
  state.selectedFilePath = null;
  state.detail = null;
  state.coverage = null;
  state.expandedTreePaths = new Set([""]);
  state.expandedSessionIds = new Set();
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
  const byId = new Map(sessions.map((session) => [session.id, session]));
  const childrenByParent = new Map();
  for (const session of sessions) {
    if (!session.parent_session_id || !byId.has(session.parent_session_id)) continue;
    const children = childrenByParent.get(session.parent_session_id) || [];
    children.push(session);
    childrenByParent.set(session.parent_session_id, children);
  }
  const roots = sessions.filter(
    (session) => !session.parent_session_id || !byId.has(session.parent_session_id),
  );
  const rendered = new Set();
  for (const session of roots) {
    list.appendChild(renderSessionNode(session, childrenByParent, rendered));
  }
  for (const session of sessions) {
    if (!rendered.has(session.id)) {
      list.appendChild(renderSessionNode(session, childrenByParent, rendered));
    }
  }
  els.threadSelector.appendChild(list);
}

function renderSessionNode(session, childrenByParent, rendered) {
  rendered.add(session.id);
  const wrapper = document.createElement("div");
  wrapper.className = "session-node";
  const children = (childrenByParent.get(session.id) || []).filter(
    (child) => !rendered.has(child.id),
  );
  const expanded = children.length > 0 && state.expandedSessionIds.has(session.id);
  const row = document.createElement("div");
  row.className = `thread-row${state.selectedSessionIds.has(session.id) ? " selected" : ""}`;
  const expander = document.createElement(children.length ? "button" : "span");
  expander.className = `session-expander${children.length ? "" : " empty"}`;
  if (children.length) {
    expander.type = "button";
    expander.textContent = expanded ? "-" : "+";
    expander.title = expanded ? "Collapse child agents" : "Expand child agents";
    expander.setAttribute("aria-expanded", String(expanded));
    expander.addEventListener("click", () => {
      if (expanded) state.expandedSessionIds.delete(session.id);
      else state.expandedSessionIds.add(session.id);
      renderSessionSelector(state.detail.sessions);
      saveState();
    });
  }
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = state.selectedSessionIds.has(session.id);
  checkbox.title = "Include only this agent's Read coverage";
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) state.selectedSessionIds.add(session.id);
    else state.selectedSessionIds.delete(session.id);
    loadCoverage();
  });
  const main = document.createElement("div");
  main.className = "thread-main";
  const title = document.createElement("div");
  title.className = "thread-id";
  const displayTitle = typeof session.session_title === "string"
    ? session.session_title.trim()
    : "";
  title.textContent = displayTitle
    ? `${session.agent_type} · ${displayTitle}`
    : `${session.agent_type} · ${shortId(session.agent_session_id)}`;
  title.title = displayTitle
    ? `${displayTitle}\n${session.agent_session_id}`
    : session.agent_session_id;
  const sub = document.createElement("div");
  sub.className = "thread-targets";
  const kind = children.length
    ? `${children.length} child agent${children.length === 1 ? "" : "s"}`
    : session.parent_session_id ? "child agent" : "agent session";
  sub.textContent = `${kind} · ${shortId(session.agent_session_id)} · ${formatPercent(session.percent)} | ${session.covered_lines}/${session.total_lines}`;
  main.append(title, sub);
  row.append(expander, checkbox, main);
  wrapper.appendChild(row);
  if (children.length) {
    const childList = document.createElement("div");
    childList.className = `session-children${expanded ? "" : " collapsed"}`;
    for (const child of children) {
      childList.appendChild(renderSessionNode(child, childrenByParent, rendered));
    }
    wrapper.appendChild(childList);
  }
  return wrapper;
}

function renderTree(root) {
  updateFileNavigationControls();
  els.treeView.className = "tree-view";
  if (state.fileViewMode === "files") {
    renderAllFiles(root);
    return;
  }
  els.treeView.replaceChildren(renderTreeNode(root));
}

function renderTreeNode(node) {
  if (node.type === "file") return renderFileNavigationNode(node, node.name);

  const wrapper = document.createElement("div");
  const row = document.createElement("button");
  const key = node.path || "";
  const expanded = state.expandedTreePaths.has(key);
  row.type = "button";
  row.className = "tree-row directory-row";
  row.setAttribute("aria-expanded", String(expanded));
  row.innerHTML = '<div class="tree-marker"></div><div class="tree-kind"></div><div class="file-read-badge placeholder" aria-hidden="true"></div><div class="tree-name"></div><div class="tree-percent"></div>';
  row.children[0].textContent = expanded ? "-" : "+";
  row.children[1].textContent = "DIR";
  row.children[3].textContent = node.name;
  row.children[3].title = node.path || node.name;
  row.children[4].textContent = formatPercent(node.percent);
  wrapper.appendChild(row);
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

function renderAllFiles(root) {
  const files = collectFileNodes(root);
  files.sort((left, right) => {
    const difference = state.fileSortDirection === "asc"
      ? fileMaxReadCount(left) - fileMaxReadCount(right)
      : fileMaxReadCount(right) - fileMaxReadCount(left);
    if (difference) return difference;
    return String(left.path).localeCompare(String(right.path), undefined, {
      sensitivity: "base",
      numeric: true,
    });
  });
  const fragment = document.createDocumentFragment();
  for (const file of files) {
    fragment.appendChild(renderFileNavigationNode(file, file.path, " flat-file-row"));
  }
  if (!files.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No source files in this project snapshot.";
    fragment.appendChild(empty);
  }
  els.treeView.replaceChildren(fragment);
}

function collectFileNodes(node, files = []) {
  if (node.type === "file") {
    files.push(node);
    return files;
  }
  for (const child of node.children || []) collectFileNodes(child, files);
  return files;
}

function renderFileNavigationNode(node, displayName, extraClass = "") {
  const wrapper = document.createElement("div");
  const row = document.createElement("button");
  const maxReadCount = fileMaxReadCount(node);
  row.type = "button";
  row.className = `tree-row file-row${extraClass}${node.path === state.selectedFilePath ? " active" : ""}`;
  row.innerHTML = '<div class="tree-marker"></div><div class="tree-kind"></div><div class="file-read-badge"></div><div class="tree-name"></div><div class="tree-percent"></div>';
  row.children[1].textContent = "FILE";
  renderFileReadBadge(row.children[2], maxReadCount);
  row.children[3].textContent = displayName;
  row.children[3].title = node.path;
  row.children[4].textContent = formatPercent(node.percent);
  const readLabel = maxReadCount === 1 ? "1 read" : `${maxReadCount} reads`;
  row.setAttribute(
    "aria-label",
    `${node.path}; maximum ${readLabel} on one line; ${formatPercent(node.percent)} coverage`,
  );
  if (node.path === state.selectedFilePath) row.setAttribute("aria-current", "true");
  row.addEventListener("click", () => loadFile(node.path));
  wrapper.appendChild(row);
  return wrapper;
}

function renderFileReadBadge(badge, count) {
  const label = count === 1
    ? "Maximum single-line read count: 1"
    : `Maximum single-line read count: ${count}`;
  badge.textContent = String(count);
  badge.className = `file-read-badge ${count > 0 ? "read" : "unread"}`;
  badge.title = label;
  badge.setAttribute("aria-hidden", "true");
  if (count > 0) badge.style.setProperty("--file-read-color", fileReadCountColor(count));
}

function renderFile(file) {
  els.filePath.textContent = file.path;
  els.fileStats.textContent = `${formatPercent(file.percent)} | ${file.covered_lines} / ${file.total_lines} lines`;
  if (file.content_changed) els.fileStats.textContent += " | snapshot changed";
  els.codeView.className = "code-view";
  els.codeView.replaceChildren();
  const fragment = document.createDocumentFragment();
  for (const line of file.lines) {
    const readCount = Math.max(normalizedReadCount(line), line.covered ? 1 : 0);
    const covered = Boolean(line.covered) || readCount > 0;
    const row = document.createElement("div");
    row.className = `code-line ${covered ? "covered" : "uncovered"}`;
    const number = document.createElement("div");
    number.className = "line-number";
    number.textContent = line.number;
    const rail = document.createElement("div");
    rail.className = "line-rail";
    if (covered) {
      const label = readCount === 1 ? "Read 1 time" : `Read ${readCount} times`;
      const heat = readHeat(readCount);
      rail.title = label;
      rail.setAttribute("role", "img");
      rail.setAttribute("aria-label", label);
      row.style.setProperty("--covered-rail-color", heat.railColor);
      row.style.setProperty("--covered-row-alpha", heat.rowAlpha);
    }
    const code = document.createElement("div");
    code.className = "line-code";
    code.textContent = line.text || " ";
    row.append(number, rail, code);
    fragment.appendChild(row);
  }
  els.codeView.appendChild(fragment);
}

function renderEmpty() {
  const hasProjects = state.projects.length > 0;
  clearProjectSelection();
  saveState();
  els.projectKicker.textContent = "No project selected";
  els.projectTitle.textContent = "Audit coverage viewer";
  els.projectMeta.textContent = "";
  els.metricGrid.replaceChildren();
  els.threadSelector.className = "thread-selector empty-state";
  els.threadSelector.textContent = hasProjects
    ? "Select a project from the sidebar."
    : "Create a repository project first.";
  els.treeView.className = "tree-view empty-state";
  els.treeView.textContent = hasProjects
    ? "No project selected."
    : "No project snapshots found.";
  els.filePath.textContent = "Select a file from the target tree.";
  els.fileStats.textContent = "";
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

async function deleteJson(url) {
  const response = await fetch(url, { method: "DELETE" });
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
function normalizedReadCount(line) {
  const value = Number(line.read_count ?? (line.covered ? 1 : 0));
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}
function fileMaxReadCount(file) {
  const value = Number(file.max_read_count ?? 0);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}
function fileReadCountColor(readCount) {
  // Keep 1 readable, make 1 -> 2 and 2 -> 3 visibly darker, then taper off.
  const depth = 1 - Math.exp(-0.75 * (Math.max(1, readCount) - 1));
  const saturation = 60 + depth * 16;
  const lightness = 34 - depth * 16;
  return `hsl(158 ${saturation}% ${lightness}%)`;
}
function readHeat(readCount) {
  // Front-load contrast so 1 -> 2 and 2 -> 3 reads are immediately visible,
  // then approach a stable dark green as the count keeps increasing.
  const depth = 1 - Math.exp(-0.75 * (Math.max(1, readCount) - 1));
  const saturation = 52 + depth * 18;
  const lightness = 56 - depth * 32;
  return {
    railColor: `hsl(158 ${saturation}% ${lightness}%)`,
    rowAlpha: String(0.08 + depth * 0.26),
  };
}
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
    leftColumnWidth: state.leftColumnWidth,
    fileViewMode: state.fileViewMode,
    fileSortDirection: state.fileSortDirection,
    expandedTreePaths: [...state.expandedTreePaths],
    expandedSessionIds: [...state.expandedSessionIds],
  }));
}
function restoreState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    if (Number.isInteger(saved.selectedProjectId)) state.selectedProjectId = saved.selectedProjectId;
    if (Array.isArray(saved.selectedSessionIds)) state.selectedSessionIds = new Set(saved.selectedSessionIds);
    if (typeof saved.selectedFilePath === "string") state.selectedFilePath = saved.selectedFilePath;
    if (Number.isFinite(Number(saved.leftColumnWidth)) && Number(saved.leftColumnWidth) > 0) {
      state.leftColumnWidth = Number(saved.leftColumnWidth);
    }
    if (saved.fileViewMode === "tree" || saved.fileViewMode === "files") {
      state.fileViewMode = saved.fileViewMode;
    }
    if (saved.fileSortDirection === "asc" || saved.fileSortDirection === "desc") {
      state.fileSortDirection = saved.fileSortDirection;
    }
    if (Array.isArray(saved.expandedTreePaths)) state.expandedTreePaths = new Set(saved.expandedTreePaths);
    if (Array.isArray(saved.expandedSessionIds)) state.expandedSessionIds = new Set(saved.expandedSessionIds);
  } catch (_error) { return; }
}
