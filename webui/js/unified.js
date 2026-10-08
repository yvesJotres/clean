/**
 * Unified File Explorer + Bloat Cleaner
 * Single-page client for webui/server.py
 *
 * Endpoints used (all existing unless noted):
 *   GET  /api/drives                → { drives: [{ drive, label, used, total, free }] }
 *   GET  /api/user-home             → { home }                (added for Quick Access)
 *   GET  /api/scan?path&sort&filter&mode=fast|detail
 *                                    → { items: [{ name, size, files, path, junk, isDir? }], stats }
 *                                      mode=fast returns size/files as null (no recursion)
 *   POST /api/get-sizes             → { sizes: {path: n}, counts: {path: n} }  (added: phase-2 sizes)
 *   GET  /api/delete?path=…         → { ok } | { error }      (server only implements GET)
 *   GET  /api/analyze-c             → { summary, cache, move, decide }
 *   GET  /api/open?path=…           → { ok } | { error }
 */
'use strict';

// ---------- Constants ----------
const LS_KEY = 'clean.webui.v1';
const DEFAULT_PATH = 'C:\\';

// Mirrors folder_size_config.py — advisory only, the server enforces the real rules.
const PROTECTED_PATHS = [
  'C:\\Windows',
  'C:\\Program Files',
  'C:\\Program Files (x86)',
  'C:\\ProgramData',
  'C:\\Users',
  'C:\\Recovery',
  'C:\\System Volume Information',
  'C:\\Documents and Settings',
  'C:\\OneDriveTemp',
  'C:\\PerfLogs',
  'C:\\Boot',
  'C:\\EFI',
  'D:\\AI',
];

// Folder names hidden when "Hide system" is enabled.
const SYSTEM_FOLDER_NAMES = new Set([
  'windows', 'program files', 'program files (x86)', 'programdata',
  '$recycle.bin', 'system volume information', 'documents and settings',
  'recovery', 'perflogs', 'boot', 'efi',
]);

// ---------- State ----------
const state = {
  mode: 'explorer',
  currentPath: DEFAULT_PATH,
  history: [DEFAULT_PATH],
  historyIndex: 0,
  sort: 'size',
  sortKey: 'size',
  sortDir: 'desc',
  filterLargeOnly: false,
  filterHideSystem: false,
  search: '',
  selection: new Set(),
  anchorIndex: null,
  cursorPath: null,
  sidebarCollapsed: false,
  items: [],
  drives: [],
  userHome: '',
  loading: false,
  requestId: 0,
  sizesPending: 0,
  autoScan: false,
  notify: false,
  cleanerData: null,
  cleanerLoading: false,
  contextPath: null,
  editingPath: false,
};

function persist() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({
      mode: state.mode,
      currentPath: state.currentPath,
      sort: state.sort,
      sortKey: state.sortKey,
      sortDir: state.sortDir,
      filterLargeOnly: state.filterLargeOnly,
      filterHideSystem: state.filterHideSystem,
      sidebarCollapsed: state.sidebarCollapsed,
      autoScan: state.autoScan,
      notify: state.notify,
    }));
  } catch (_) { /* storage unavailable */ }
}

function restore() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return;
    const d = JSON.parse(raw);
    if (d.mode === 'explorer' || d.mode === 'cleaner') state.mode = d.mode;
    if (typeof d.currentPath === 'string' && d.currentPath) state.currentPath = d.currentPath;
    if (d.sort === 'size' || d.sort === 'name') state.sort = d.sort;
    if (d.sortKey) state.sortKey = d.sortKey;
    if (d.sortDir === 'asc' || d.sortDir === 'desc') state.sortDir = d.sortDir;
    state.filterLargeOnly = !!d.filterLargeOnly;
    state.filterHideSystem = !!d.filterHideSystem;
    state.sidebarCollapsed = !!d.sidebarCollapsed;
    state.autoScan = !!d.autoScan;
    state.notify = !!d.notify;
    state.history = [state.currentPath];
    state.historyIndex = 0;
  } catch (_) { /* corrupted storage, use defaults */ }
}
// ---------- Utilities ----------
function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 1 : 2)} ${units[unitIndex]}`;
}

function escapeHtml(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function basename(p) {
  const parts = String(p || '').replace(/\//g, '\\').replace(/\\+$/, '').split('\\');
  return parts[parts.length - 1] || String(p || '');
}

function shortenPath(p, max = 64) {
  const s = String(p || '');
  if (s.length <= max) return s;
  const head = Math.ceil((max - 3) / 2);
  const tail = max - 3 - head;
  return `${s.slice(0, head)}...${s.slice(s.length - tail)}`;
}

function parentPath(p) {
  const stripped = String(p || '').replace(/[\\/]+$/, '');
  const idx = stripped.lastIndexOf('\\');
  if (idx <= 0) return p;
  let parent = stripped.slice(0, idx);
  if (/^[A-Za-z]:$/.test(parent)) parent += '\\';
  return parent || p;
}

function isProtectedPath(p) {
  const bare = String(p || '').replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase();
  const bases = PROTECTED_PATHS.slice();
  if (state.userHome) bases.push(`${state.userHome}\\AppData\\Local\\Microsoft`);
  return bases.some((b) => {
    const bb = b.replace(/\\+$/, '').toLowerCase();
    return bare === bb || bare.startsWith(`${bb}\\`);
  });
}

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status || 0;
  }
}

async function fetchJson(url, opts) {
  const response = await fetch(url, opts);
  let json = {};
  try { json = await response.json(); } catch (_) { /* non-JSON body */ }
  if (!response.ok) {
    throw new ApiError(json.error || `Request failed (${response.status})`, response.status);
  }
  return json;
}

function deleteApi(path) {
  // The server exposes /api/delete via GET only (no do_DELETE handler).
  return fetchJson(`/api/delete?path=${encodeURIComponent(path)}`);
}

function isPermissionError(err) {
  return /access is denied|permission|not permitted|denied/i.test(String(err && err.message || ''));
}
// ---------- Toasts (max 5 visible, errors last 8s) ----------
function toast(message, type = 'info', duration) {
  const container = document.getElementById('toasts');
  if (!container) return;
  const ms = duration != null ? duration : (type === 'error' ? 8000 : 4000);
  while (container.children.length >= 5) container.firstElementChild.remove();

  const el = document.createElement('div');
  el.className = `toast ${type}`;
  const icon = type === 'success' ? '✓' : type === 'error' ? '✕' : type === 'warning' ? '⚠' : 'ℹ';
  el.innerHTML = `
    <div class="toast-icon" aria-hidden="true">${icon}</div>
    <div class="toast-message"></div>
    <button class="toast-close" aria-label="Dismiss">&times;</button>`;
  el.querySelector('.toast-message').textContent = message;
  el.querySelector('.toast-close').addEventListener('click', () => dismissToast(el));
  container.appendChild(el);
  if (ms > 0) setTimeout(() => dismissToast(el), ms);
}

function dismissToast(el) {
  if (!el || !el.parentElement) return;
  el.classList.add('exiting');
  setTimeout(() => el.remove(), 200);
}

// ---------- Confirmation dialog ----------
function confirmDialog({ title = 'Confirm', message = '', warning = '', okText = 'OK', danger = true } = {}) {
  return new Promise((resolve) => {
    const overlay = document.getElementById('confirmModal');
    const titleEl = document.getElementById('confirmTitle');
    const warningEl = document.getElementById('confirmWarning');
    const messageEl = document.getElementById('confirmMessage');
    const okBtn = document.getElementById('confirmOk');
    const cancelBtn = document.getElementById('confirmCancel');
    const xBtn = document.getElementById('confirmX');

    titleEl.textContent = title;
    messageEl.textContent = message;
    if (warning) {
      warningEl.textContent = warning;
      warningEl.classList.remove('hidden');
    } else {
      warningEl.classList.add('hidden');
    }
    okBtn.textContent = okText;
    okBtn.className = danger ? 'btn btn-danger' : 'btn btn-primary';
    overlay.classList.add('visible');
    okBtn.focus();

    let settled = false;
    function close(value) {
      if (settled) return;
      settled = true;
      overlay.classList.remove('visible');
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      xBtn.removeEventListener('click', onCancel);
      overlay.removeEventListener('click', onOverlay);
      document.removeEventListener('keydown', onKey, true);
      resolve(value);
    }
    function onOk() { close(true); }
    function onCancel() { close(false); }
    function onOverlay(e) { if (e.target === overlay) close(false); }
    function onKey(e) {
      if (e.key === 'Enter') { e.preventDefault(); close(true); }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(false); }
    }

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    xBtn.addEventListener('click', onCancel);
    overlay.addEventListener('click', onOverlay);
    document.addEventListener('keydown', onKey, true);
  });
}
// ---------- DOM ----------
const $ = (id) => document.getElementById(id);
const shell = $('appShell');
const els = {
  btnSidebar: $('btnSidebar'), sidebar: $('sidebar'), sidebarOverlay: $('sidebarOverlay'),
  tabExplorer: $('tabExplorer'), tabCleaner: $('tabCleaner'), titlebarHint: $('titlebarHint'),
  driveList: $('driveList'), btnScanCSidebar: $('btnScanCSidebar'),
  chkAutoScan: $('chkAutoScan'), chkNotify: $('chkNotify'),
  btnBack: $('btnBack'), btnForward: $('btnForward'),
  pathBar: $('pathBar'), breadcrumb: $('breadcrumb'), pathInput: $('pathInput'),
  toolbarSpinner: $('toolbarSpinner'),
  searchInput: $('searchInput'), sortSelect: $('sortSelect'),
  chkLargeOnly: $('chkLargeOnly'), chkHideSystem: $('chkHideSystem'),
  btnScanC: $('btnScanC'), btnRescan: $('btnRescan'), btnScanIdle: $('btnScanIdle'),
  explorerView: $('explorerView'), cleanerView: $('cleanerView'),
  tableWrap: $('tableWrap'), resultsBody: $('resultsBody'), chkSelectAll: $('chkSelectAll'),
  explorerEmpty: $('explorerEmpty'), explorerEmptyTitle: $('explorerEmptyTitle'), explorerEmptyDesc: $('explorerEmptyDesc'),
  summary: $('summary'), categoryGrid: $('categoryGrid'), cleanerIdle: $('cleanerIdle'),
  sumDelete: $('sumDelete'), sumMove: $('sumMove'), sumDecide: $('sumDecide'),
  countDelete: $('countDelete'), countMove: $('countMove'), countDecide: $('countDecide'),
  listDelete: $('listDelete'), listMove: $('listMove'), listDecide: $('listDecide'),
  statusBar: $('statusBar'), statusText: $('statusText'), progressBar: $('progressBar'),
  selectionInfo: $('selectionInfo'), btnDeleteSelected: $('btnDeleteSelected'), btnClearSelection: $('btnClearSelection'),
  contextMenu: $('contextMenu'),
  fileModal: $('fileModal'), modalBack: $('modalBack'), modalTitle: $('modalTitle'),
  modalPath: $('modalPath'), modalClose: $('modalClose'), modalContent: $('modalContent'),
};

function setStatus(message) {
  if (els.statusText) els.statusText.textContent = message;
}

function updateLoadingUI() {
  els.toolbarSpinner.classList.toggle('hidden', !state.loading && !state.cleanerLoading);
  els.statusBar.classList.toggle('is-loading', state.loading || state.cleanerLoading || state.sizesPending > 0);
}

// ---------- Mode switch ----------
function setMode(mode, { persist: doPersist = true } = {}) {
  state.mode = mode;
  const isCleaner = mode === 'cleaner';
  shell.classList.toggle('mode-explorer', !isCleaner);
  shell.classList.toggle('mode-cleaner', isCleaner);
  els.tabExplorer.classList.toggle('active', !isCleaner);
  els.tabCleaner.classList.toggle('active', isCleaner);
  els.tabExplorer.setAttribute('aria-selected', String(!isCleaner));
  els.tabCleaner.setAttribute('aria-selected', String(isCleaner));
  els.explorerView.classList.toggle('hidden', isCleaner);
  els.cleanerView.classList.toggle('hidden', !isCleaner);
  els.titlebarHint.textContent = isCleaner ? 'Bloat Cleaner' : 'File Explorer';

  if (isCleaner && !state.cleanerData && !state.cleanerLoading && state.autoScan) {
    runCleanerScan();
  }
  if (doPersist) persist();
}

// ---------- Sidebar ----------
function isDrawerMode() { return window.innerWidth < 768; }

function toggleSidebar() {
  if (isDrawerMode()) {
    const open = !shell.classList.contains('drawer-open');
    shell.classList.toggle('drawer-open', open);
    els.sidebarOverlay.classList.toggle('hidden', !open);
  } else {
    state.sidebarCollapsed = !state.sidebarCollapsed;
    shell.classList.toggle('sidebar-collapsed', state.sidebarCollapsed);
    persist();
  }
}

function closeDrawer() {
  shell.classList.remove('drawer-open');
  els.sidebarOverlay.classList.add('hidden');
}

function quickPath(key) {
  const home = state.userHome || 'C:\\Users';
  if (key === 'home') return home;
  const map = {
    downloads: 'Downloads', documents: 'Documents', pictures: 'Pictures', videos: 'Videos',
  };
  return `${home}\\${map[key] || ''}`;
}

async function loadUserHome() {
  try {
    const data = await fetchJson('/api/user-home');
    if (data.home) state.userHome = String(data.home).replace(/\\+$/, '');
  } catch (_) {
    state.userHome = ''; // older server — Quick Access falls back to C:\Users
  }
}

async function loadDrives() {
  try {
    const data = await fetchJson('/api/drives');
    state.drives = data.drives || [];
    renderDrives();
  } catch (err) {
    els.driveList.innerHTML = `<div class="drive-label">Drives unavailable: ${escapeHtml(err.message)}</div>`;
  }
}

function renderDrives() {
  const current = state.currentPath.replace(/\//g, '\\').toLowerCase();
  els.driveList.innerHTML = '';
  state.drives.forEach((d) => {
    const used = Number(d.used || 0);
    const total = Number(d.total || 1) || 1;
    const percent = Math.min(100, Math.max(0, (used / total) * 100));
    const freeGb = Number(d.free || 0) / 1024 / 1024 / 1024;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'drive-item';
    if (current.startsWith(String(d.drive).toLowerCase())) btn.classList.add('active');
    const fillClass = percent > 90 ? 'crit' : percent > 75 ? 'high' : '';
    btn.innerHTML = `
      <div class="drive-meta">
        <span class="drive-name">${escapeHtml(d.drive)}</span>
        <span class="drive-free">${freeGb.toFixed(0)} GB free</span>
      </div>
      <div class="drive-label">${escapeHtml(d.label || 'Local Disk')}</div>
      <div class="drive-bar"><div class="drive-bar-fill ${fillClass}" style="width:${percent.toFixed(0)}%"></div></div>`;
    btn.title = `${d.drive} ${d.label || ''} — ${freeGb.toFixed(1)} GB free of ${formatSize(total)}`;
    btn.addEventListener('click', () => {
      navigate(d.drive);
      if (isDrawerMode()) closeDrawer();
    });
    els.driveList.appendChild(btn);
  });
}
// ---------- Breadcrumb / path input ----------
function buildSegments(p) {
  const norm = String(p || '').replace(/\//g, '\\');
  const segs = [];
  const m = norm.match(/^([A-Za-z]:\\)(.*)$/);
  if (m) {
    segs.push({ label: m[1], path: m[1] });
    let acc = m[1];
    m[2].split('\\').filter(Boolean).forEach((part) => {
      acc = acc.endsWith('\\') ? acc + part : `${acc}\\${part}`;
      segs.push({ label: part, path: acc });
    });
  } else if (norm) {
    segs.push({ label: norm, path: norm });
  }
  return segs;
}

function renderBreadcrumb() {
  const segs = buildSegments(state.currentPath);
  els.breadcrumb.innerHTML = segs
    .map((s, i) => `${i ? '<span class="crumb-sep">›</span>' : ''}<button type="button" class="crumb" data-path="${escapeHtml(s.path)}">${escapeHtml(s.label)}</button>`)
    .join('');
}

function startPathEdit() {
  if (state.editingPath) return;
  state.editingPath = true;
  els.pathBar.classList.add('hidden');
  els.pathInput.classList.remove('hidden');
  els.pathInput.value = state.currentPath;
  els.pathInput.focus();
  els.pathInput.select();
}

function endPathEdit(commit) {
  if (!state.editingPath) return;
  state.editingPath = false;
  els.pathBar.classList.remove('hidden');
  els.pathInput.classList.add('hidden');
  if (commit) {
    const value = els.pathInput.value.trim();
    if (value && value.replace(/\//g, '\\') !== state.currentPath) navigate(value);
  }
}

// ---------- Navigation / history ----------
function pushHistory(path) {
  if (state.history[state.historyIndex] === path) return;
  state.history = state.history.slice(0, state.historyIndex + 1);
  state.history.push(path);
  if (state.history.length > 50) state.history.shift();
  state.historyIndex = state.history.length - 1;
}

function updateNavButtons() {
  els.btnBack.disabled = state.historyIndex <= 0;
  els.btnForward.disabled = state.historyIndex >= state.history.length - 1;
}

async function navigate(path, { push = true } = {}) {
  if (!path) return false;
  const target = String(path).replace(/\//g, '\\');
  const requestId = ++state.requestId;

  state.loading = true;
  updateLoadingUI();
  setStatus(`Scanning ${target}…`);

  const params = new URLSearchParams({
    path: target,
    sort: state.sort,
    filter: state.filterLargeOnly ? '1' : '0',
    // The size filter needs real sizes up front; otherwise render instantly.
    mode: state.filterLargeOnly ? 'detail' : 'fast',
  });

  try {
    const data = await fetchJson(`/api/scan?${params.toString()}`);
    if (requestId !== state.requestId) return false; // a newer navigation superseded this one

    state.items = data.items || [];
    state.currentPath = target;
    state.selection.clear();
    state.cursorPath = null;
    state.anchorIndex = null;
    if (push) pushHistory(target);
    persist();
    renderExplorer();
    renderBreadcrumb();
    renderDrives();
    updateNavButtons();
    setStatus(`Found ${state.items.length} folder(s) in ${target}`);
    // Phase 2 — background size loading; never blocks navigation.
    fetchSizesForRows(requestId);
    return true;
  } catch (err) {
    if (requestId !== state.requestId) return false;
    setStatus(`Failed to load ${target}`);
    toast(`Failed to open ${target}: ${err.message}`, 'error');
    return false;
  } finally {
    if (requestId === state.requestId) {
      state.loading = false;
      updateLoadingUI();
    }
  }
}

async function jumpHistory(delta) {
  const idx = state.historyIndex + delta;
  if (idx < 0 || idx >= state.history.length) return;
  const ok = await navigate(state.history[idx], { push: false });
  if (ok) {
    state.historyIndex = idx;
    updateNavButtons();
  }
}

function goUp() {
  const parent = parentPath(state.currentPath);
  if (parent !== state.currentPath) navigate(parent);
}

// ---------- Phase 2: lazy size loading ----------
async function fetchSizesForRows(requestId) {
  // Get all items that still need size/file count (both folders and files)
  const pendingItems = state.items.filter((it) => it.size == null || it.files == null);
  if (!pendingItems.length) {
    // All items already have sizes - we're in detail mode
    return;
  }

  const pending = pendingItems.map((it) => it.path);
  const totalItems = state.items.length;
  state.sizesPending = pending.length;
  state.sizesTotal = totalItems;
  state.sizesResolved = totalItems - pending.length;
  updateLoadingUI();
  setStatus(`Loading sizes… ${state.sizesResolved}/${state.sizesTotal} items resolved`);

  // Process in batches of 50 for progressive updates
  const BATCH_SIZE = 50;
  let hasError = false;

  for (let i = 0; i < pending.length; i += BATCH_SIZE) {
    // Check if navigation happened while we were loading
    if (requestId !== state.requestId) return;

    const batch = pending.slice(i, i + BATCH_SIZE);
    try {
      const data = await fetchJson('/api/get-sizes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: batch }),
      });

      const sizes = data.sizes || {};
      const counts = data.counts || {};
      let batchResolved = 0;

      state.items.forEach((it) => {
        // Only update items that are in this batch
        if (!batch.includes(it.path)) return;
        if (Object.prototype.hasOwnProperty.call(sizes, it.path)) {
          it.size = sizes[it.path];
        } else {
          it.size = 0; // vanished or inaccessible
        }
        if (Object.prototype.hasOwnProperty.call(counts, it.path)) {
          it.files = counts[it.path];
        } else {
          it.files = 0;
        }
        if (it.size != null || it.files != null) {
          batchResolved += 1;
        }
      });

      state.sizesResolved += batchResolved;
      patchSizeCells();
      setStatus(`Loading sizes… ${state.sizesResolved}/${state.sizesTotal} items resolved`);

      // Small delay to allow UI to update
      await new Promise(r => setTimeout(r, 50));
    } catch (err) {
      hasError = true;
      if (requestId !== state.requestId) return;
      console.error('Size batch load failed:', err);
    }
  }

  if (!hasError) {
    setStatus(`Sizes loaded for ${state.sizesTotal} item(s)`);
  } else {
    setStatus('Some sizes failed to load');
  }

  state.sizesPending = 0;
  updateLoadingUI();
}

function patchSizeCells() {
  const rows = els.resultsBody.querySelectorAll('tr[data-path]');
  rows.forEach((tr) => {
    const item = state.items.find((it) => it.path === tr.dataset.path);
    if (!item || item.size == null) return;
    const sizeCell = tr.querySelector('td.c-size');
    const filesCell = tr.querySelector('td.c-files');
    if (sizeCell) sizeCell.textContent = formatSize(item.size);
    if (filesCell) filesCell.textContent = Number(item.files || 0).toLocaleString();
  });
}

// ---------- Explorer: filtering / sorting ----------
function getVisibleItems() {
  const term = state.search.trim().toLowerCase();
  const rows = state.items.filter((item) => {
    if (term) {
      const hay = `${item.name} ${item.path}`.toLowerCase();
      if (!hay.includes(term)) return false;
    }
    if (state.filterHideSystem && SYSTEM_FOLDER_NAMES.has(String(item.name).toLowerCase())) return false;
    return true;
  });

  const dir = state.sortDir === 'asc' ? 1 : -1;
  rows.sort((a, b) => {
    const key = state.sortKey;
    let av; let bv;
    if (key === 'name' || key === 'path') {
      av = String(a[key] || '').toLowerCase();
      bv = String(b[key] || '').toLowerCase();
    } else if (key === 'junk') {
      av = a.junk ? 1 : 0;
      bv = b.junk ? 1 : 0;
    } else {
      av = Number(a[key] || 0);
      bv = Number(b[key] || 0);
    }
    if (av < bv) return -1 * dir;
    if (av > bv) return 1 * dir;
    return 0;
  });
  return rows;
}
// ---------- Explorer: rendering ----------
function renderExplorer() {
  const rows = getVisibleItems();

  // Header sort indicators
  document.querySelectorAll('th[data-sort]').forEach((th) => {
    th.classList.remove('sorted-asc', 'sorted-desc');
    if (th.dataset.sort === state.sortKey) {
      th.classList.add(state.sortDir === 'asc' ? 'sorted-asc' : 'sorted-desc');
    }
  });

  if (state.loading && state.items.length === 0) {
    els.tableWrap.classList.remove('hidden');
    els.explorerEmpty.classList.add('hidden');
    els.resultsBody.innerHTML = Array.from({ length: 8 }, () =>
      '<tr class="empty-row"><td colspan="5"><div class="skeleton skeleton-row" style="margin:0"></div></td></tr>'
    ).join('');
    renderSelectionUI();
    return;
  }

  if (rows.length === 0) {
    els.tableWrap.classList.add('hidden');
    els.explorerEmpty.classList.remove('hidden');
    if (state.items.length === 0) {
      els.explorerEmptyTitle.textContent = 'No folders found';
      els.explorerEmptyDesc.textContent = 'This folder may be empty or access was denied.';
    } else {
      els.explorerEmptyTitle.textContent = 'No items match your filters';
      els.explorerEmptyDesc.textContent = 'Adjust the search term or filter checkboxes and try again.';
    }
    renderSelectionUI();
    return;
  }

  els.tableWrap.classList.remove('hidden');
  els.explorerEmpty.classList.add('hidden');

  els.resultsBody.innerHTML = rows.map((item) => {
    const path = item.path;
    const isDir = item.isDir !== false;
    const selected = state.selection.has(path);
    const prot = isProtectedPath(path);
    const isFile = !isDir;
    const sizePending = item.size == null;
    const filesPending = item.files == null;

    return `
      <tr data-path="${escapeHtml(path)}" data-isdir="${isDir ? '1' : '0'}" class="${selected ? 'selected' : ''}${state.cursorPath === path ? ' cursor' : ''}${isFile ? ' is-file' : ''}">
        <td class="col-check"><input type="checkbox" data-check="1" ${selected ? 'checked' : ''} aria-label="Select ${escapeHtml(item.name)}" /></td>
        <td class="c-name"><span class="row-icon" aria-hidden="true">${isDir ? '📁' : '📄'}</span><span class="row-name" title="${escapeHtml(path)}">${escapeHtml(item.name)}</span>${item.junk ? '<span class="badge-junk">Junk</span>' : ''}${prot ? '<span class="badge-admin" title="Protected system location — deleting requires confirmation and may need Administrator">Admin</span>' : ''}</td>
        <td class="c-size">${sizePending ? '<span class="size-pending">⟳ —</span>' : formatSize(item.size)}</td>
        <td class="c-files">${filesPending ? '<span class="size-pending">⟳ —</span>' : Number(item.files).toLocaleString()}</td>
        <td class="c-path"><span class="path-text" title="${escapeHtml(path)}">${escapeHtml(shortenPath(path))}</span></td>
      </tr>`;
  }).join('');

  renderSelectionUI();
}

function renderSelectionUI() {
  const n = state.selection.size;
  els.selectionInfo.textContent = `${n} item${n === 1 ? '' : 's'} selected`;
  els.selectionInfo.classList.toggle('hidden', n === 0);
  els.btnDeleteSelected.classList.toggle('hidden', n === 0);
  els.btnClearSelection.classList.toggle('hidden', n === 0);

  const visible = getVisibleItems();
  const selectedVisible = visible.filter((it) => state.selection.has(it.path)).length;
  els.chkSelectAll.checked = visible.length > 0 && selectedVisible === visible.length;
  els.chkSelectAll.indeterminate = selectedVisible > 0 && selectedVisible < visible.length;
}

function visibleRowPaths() {
  return getVisibleItems().map((it) => it.path);
}

// ---------- Explorer: open / actions ----------
function openItemRow(path, isDir) {
  if (isDir) {
    navigate(path);
  } else {
    openWithSystem(path);
  }
}

async function openWithSystem(path) {
  try {
    await fetchJson(`/api/open?path=${encodeURIComponent(path)}`);
    setStatus(`Opening ${path}`);
    toast(`Opening ${basename(path)}`, 'info', 3000);
  } catch (err) {
    toast(`Cannot open ${basename(path)}: ${err.message}`, 'error');
  }
}
// ---------- Deletion (both modes) ----------
async function requestDelete(paths) {
  if (!paths || paths.length === 0) return;

  const protectedPaths = paths.filter((p) => isProtectedPath(p));
  const message = paths.length === 1
    ? `Delete this item permanently?\n\n${paths[0]}`
    : `Delete ${paths.length} selected items permanently?`;
  const warning = protectedPaths.length
    ? `⚠ Protected location${protectedPaths.length > 1 ? 's' : ''} in selection:\n${protectedPaths.join('\n')}\nDeletion may be blocked by the server or require Administrator.`
    : '';

  if (protectedPaths.length) {
    toast('Warning: selection includes protected system paths', 'warning', 6000);
  }

  const ok = await confirmDialog({
    title: protectedPaths.length ? 'Confirm protected delete' : 'Confirm delete',
    message,
    warning,
    okText: paths.length > 1 ? `Delete ${paths.length} items` : 'Delete',
    danger: true,
  });
  if (!ok) return;

  const deleted = [];
  let failures = 0;
  for (const p of paths) {
    try {
      await deleteApi(p);
      deleted.push(p);
      toast(`Deleted: ${basename(p)}`, 'success', 4000);
    } catch (err) {
      failures += 1;
      const msg = isPermissionError(err)
        ? `Permission denied: ${p}`
        : `Delete failed: ${p} — ${err.message}`;
      toast(msg, 'error');
    }
  }

  state.selection.clear();
  state.cursorPath = null;

  if (state.mode === 'explorer') {
    // Optimistic update — avoid an expensive full re-scan of the folder.
    const deletedSet = new Set(deleted);
    state.items = state.items.filter((it) => !deletedSet.has(it.path));
    renderExplorer();
    setStatus(`Deleted ${deleted.length} item(s)${failures ? `, ${failures} failed` : ''}`);
  } else if (deleted.length) {
    // Cleaner mode: re-run the analysis so totals stay accurate.
    runCleanerScan();
  }
}

function deleteSelection() {
  const paths = state.selection.size
    ? [...state.selection]
    : (state.cursorPath ? [state.cursorPath] : []);
  requestDelete(paths);
}

// ---------- Cleaner mode ----------
function normalizeSummary(summary) {
  const s = summary || {};
  return {
    del: s.delete != null ? s.delete : (s.cache || 0),
    move: s.move || 0,
    decide: s.decide || 0,
    delCount: s.deleteCount != null ? s.deleteCount : (s.cacheCount || 0),
    moveCount: s.moveCount || 0,
    decideCount: s.decideCount || 0,
  };
}

function showSummarySkeleton() {
  ['sumDelete', 'sumMove', 'sumDecide'].forEach((id) => {
    $(id).innerHTML = '<div class="skeleton" style="width:90px;height:30px;margin:0 auto"></div>';
  });
  ['countDelete', 'countMove', 'countDecide'].forEach((id) => {
    $(id).innerHTML = '<div class="skeleton" style="width:70px;height:12px;margin:4px 0 0 auto;display:block"></div>';
  });
}

function showListSkeleton(container) {
  container.innerHTML = Array.from({ length: 4 }, () =>
    '<div class="skeleton skeleton-row"></div>').join('');
}

function updateCleanerControls() {
  const loading = state.cleanerLoading;
  [els.btnScanC, els.btnScanCSidebar, els.btnScanIdle].forEach((b) => { b.disabled = loading; });
  els.btnRescan.disabled = loading || !state.cleanerData;
  els.btnScanC.textContent = loading ? 'Scanning…' : (state.cleanerData ? 'Re-scan' : 'Scan C: Drive');
  els.btnScanCSidebar.querySelector('.btn-label').textContent = loading ? 'Scanning…' : 'Scan C: Drive';
  els.summary.classList.toggle('hidden', !state.cleanerData && !loading);
  els.categoryGrid.classList.toggle('hidden', !state.cleanerData && !loading);
  els.cleanerIdle.classList.toggle('hidden', !!state.cleanerData || loading);
  updateLoadingUI();
}

async function runCleanerScan() {
  if (state.cleanerLoading) return;
  state.cleanerLoading = true;
  updateCleanerControls();
  setStatus('Scanning C: drive bloat paths…');

  showSummarySkeleton();
  showListSkeleton(els.listDelete);
  showListSkeleton(els.listMove);
  showListSkeleton(els.listDecide);

  try {
    const data = await fetchJson('/api/analyze-c');
    state.cleanerData = {
      summary: normalizeSummary(data.summary),
      cache: data.cache || data.delete || [],
      move: data.move || [],
      decide: data.decide || [],
    };
    renderCleaner();
    const s = state.cleanerData.summary;
    setStatus('Scan complete');
    const msg = `Scan complete — ${s.delCount} deletable, ${s.moveCount} movable, ${s.decideCount} to review`;
    toast(msg, 'success');
    notifyDone('Clean scan complete', msg);
  } catch (err) {
    setStatus('Scan failed');
    toast(`Scan failed: ${err.message}`, 'error');
    if (!state.cleanerData) {
      els.summary.classList.add('hidden');
      els.categoryGrid.classList.add('hidden');
      els.cleanerIdle.classList.remove('hidden');
    }
  } finally {
    state.cleanerLoading = false;
    updateCleanerControls();
  }
}

function notifyDone(title, body) {
  if (!state.notify) return;
  try {
    if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      new Notification(title, { body });
    }
  } catch (_) { /* notifications unsupported */ }
}
function renderCleaner() {
  const data = state.cleanerData;
  if (!data) return;
  const s = data.summary;

  els.sumDelete.textContent = formatSize(s.del);
  els.sumMove.textContent = formatSize(s.move);
  els.sumDecide.textContent = formatSize(s.decide);
  els.countDelete.textContent = `${s.delCount} items`;
  els.countMove.textContent = `${s.moveCount} items`;
  els.countDecide.textContent = `${s.decideCount} items`;

  renderCleanerList(els.listDelete, data.cache, 'delete');
  renderCleanerList(els.listMove, data.move, 'move');
  renderCleanerList(els.listDecide, data.decide, 'decide');
  updateCleanerControls();
}

function renderCleanerList(container, items, type) {
  if (!items.length) {
    const labels = {
      delete: { title: 'No cache folders found', desc: 'Your C: drive is clean — no safe-to-delete items detected.' },
      move: { title: 'No movable applications', desc: 'No large apps detected that could be relocated to D: drive.' },
      decide: { title: 'Nothing requires review', desc: 'All detected items are categorized as safe to delete or move.' },
    };
    const l = labels[type] || { title: 'Nothing found', desc: '' };
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon" aria-hidden="true">✨</div>
        <div class="empty-state-title">${l.title}</div>
        <div class="empty-state-desc">${l.desc}</div>
      </div>`;
    return;
  }

  container.innerHTML = items.map((item) => {
    const btnClass = type === 'delete' ? 'btn-danger' : type === 'move' ? 'btn-warning' : 'btn-info';
    const btnLabel = type === 'delete' ? 'Delete' : type === 'move' ? 'Move' : 'Review';
    return `
      <div class="item">
        <div class="item-main">
          <div class="item-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div>
          <div class="item-desc" title="${escapeHtml(item.desc || '')}">${escapeHtml(item.desc || '')}</div>
          <div class="item-path" data-open-dir="${escapeHtml(item.path)}" title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</div>
        </div>
        <div class="item-size">${formatSize(item.size)}</div>
        <div class="item-actions">
          <button type="button" class="btn ${btnClass}" data-act="${type}" data-path="${escapeHtml(item.path)}" data-name="${escapeHtml(item.name)}">${btnLabel}</button>
        </div>
      </div>`;
  }).join('');
}

async function handleCleanerAction(type, path, name) {
  if (type === 'delete') {
    const prot = isProtectedPath(path);
    if (prot) toast(`Warning: ${name} is in a protected location`, 'warning', 6000);
    const ok = await confirmDialog({
      title: prot ? 'Confirm protected delete' : 'Confirm delete',
      message: `Delete this item permanently?\n\n${name}\n${path}`,
      warning: prot ? '⚠ This path is a protected system location.\nDeletion may be blocked or require Administrator.' : '',
      okText: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteApi(path);
      toast(`Deleted: ${name}`, 'success');
      runCleanerScan();
    } catch (err) {
      const msg = isPermissionError(err)
        ? `Permission denied: ${path}`
        : `Delete failed: ${err.message}`;
      toast(msg, 'error');
    }
  } else if (type === 'move') {
    toast(`To move "${name}" to D: drive: uninstall the app, then reinstall choosing D: as the location.`, 'info', 8000);
  } else {
    toast(`Review required for "${name}" — decide based on your usage.`, 'info', 6000);
  }
}

// ---------- File browser modal ----------
const modalStack = [];

async function openFileModal(path, name) {
  modalStack.push({ path, name });
  await renderFileModal();
}

async function renderFileModal() {
  const current = modalStack[modalStack.length - 1];
  if (!current) return;

  els.modalTitle.textContent = current.name;
  els.modalPath.textContent = current.path;
  els.modalBack.classList.toggle('hidden', modalStack.length <= 1);
  els.fileModal.classList.add('visible');
  els.modalContent.innerHTML = '<div class="modal-loading"><div class="spinner"></div>Loading folder…</div>';

  try {
    const data = await fetchJson(`/api/scan?path=${encodeURIComponent(current.path)}&sort=size`);
    const items = data.items || [];
    if (!items.length) {
      els.modalContent.innerHTML = `
        <div class="modal-empty">
          <div class="modal-empty-icon" aria-hidden="true">📁</div>
          <div>This folder is empty or access is denied</div>
        </div>`;
      return;
    }
    els.modalContent.innerHTML = `<div class="file-list">${items.map((item) => {
      const isDir = item.isDir !== false;
      return `
        <div class="file-row ${isDir ? 'folder clickable' : 'file'}" ${isDir ? `data-open-dir="${escapeHtml(item.path)}"` : ''}>
          <span class="file-icon" aria-hidden="true">${isDir ? '📁' : '📄'}</span>
          <span class="file-name" title="${escapeHtml(item.path)}">${escapeHtml(item.name)}</span>
          <span class="file-size">${formatSize(item.size)}</span>
          <span class="file-type">${isDir ? 'Folder' : (item.ext || 'File')}</span>
        </div>`;
    }).join('')}</div>`;
  } catch (err) {
    els.modalContent.innerHTML = `
      <div class="modal-empty">
        <div class="modal-empty-icon" aria-hidden="true">⚠</div>
        <div>Failed to load: ${escapeHtml(err.message)}</div>
      </div>`;
  }
}

function modalBack() {
  if (modalStack.length > 1) {
    modalStack.pop();
    renderFileModal();
  }
}

function closeFileModal() {
  modalStack.length = 0;
  els.fileModal.classList.remove('visible');
}
function openProperties(path) {
  const item = state.items.find((it) => it.path === path) || null;
  const prot = isProtectedPath(path);
  const rows = [
    ['Name', basename(path)],
    ['Path', path],
    ['Type', item ? (item.isDir === false ? 'File' : 'Folder') : 'Item'],
    ['Size', item && item.size != null ? formatSize(item.size) : '—'],
    ['Files', item && item.files != null ? Number(item.files).toLocaleString() : '—'],
    ['Junk', item && item.junk ? 'Flagged as junk/cache' : 'No'],
    ['Protected', prot ? 'Yes — requires confirmation (may need Administrator)' : 'No'],
  ];
  modalStack.push({ path: '', name: 'Properties' });
  els.modalTitle.textContent = 'Properties';
  els.modalPath.textContent = path;
  els.modalBack.classList.toggle('hidden', modalStack.length <= 1);
  els.modalContent.innerHTML = `<table class="props-table"><tbody>${rows
    .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(v)}</td></tr>`)
    .join('')}</tbody></table>`;
  els.fileModal.classList.add('visible');
}

// ---------- Context menu ----------
function showContextMenu(x, y, path) {
  state.contextPath = path;
  const menu = els.contextMenu;
  menu.classList.remove('hidden');
  const rect = menu.getBoundingClientRect();
  const left = Math.min(x, window.innerWidth - rect.width - 8);
  const top = Math.min(y, window.innerHeight - rect.height - 8);
  menu.style.left = `${Math.max(4, left)}px`;
  menu.style.top = `${Math.max(4, top)}px`;
}

function hideContextMenu() {
  els.contextMenu.classList.add('hidden');
  state.contextPath = null;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Path copied to clipboard', 'success', 2500);
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); toast('Path copied to clipboard', 'success', 2500); }
    catch (e) { toast('Copy failed', 'error'); }
    ta.remove();
  }
}

// ---------- Keyboard cursor ----------
function moveCursor(delta) {
  const paths = visibleRowPaths();
  if (!paths.length) return;
  let idx = paths.indexOf(state.cursorPath);
  if (idx === -1) idx = delta > 0 ? -1 : paths.length;
  idx = Math.max(0, Math.min(paths.length - 1, idx + delta));
  state.cursorPath = paths[idx];
  renderExplorer();
  const row = els.resultsBody.querySelector(`tr[data-path="${CSS.escape(state.cursorPath)}"]`);
  if (row) row.scrollIntoView({ block: 'nearest' });
}

function handleKeydown(e) {
  const target = e.target;
  const typing = target && (
    target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' ||
    target.tagName === 'SELECT' || target.isContentEditable
  );

  // Escape: close context menu first, then clear selection
  if (e.key === 'Escape') {
    if (!els.contextMenu.classList.contains('hidden')) { hideContextMenu(); return; }
    if (els.fileModal.classList.contains('visible')) { closeFileModal(); return; }
    if (typing && target === els.pathInput) return; // path input handles its own Escape
    if (state.selection.size || state.cursorPath) {
      state.selection.clear();
      state.cursorPath = null;
      state.anchorIndex = null;
      renderExplorer();
    }
    return;
  }

  if (typing) return;
  if (document.querySelector('.modal-overlay.visible')) return; // dialogs handle keys
  if (state.mode !== 'explorer') return;

  if ((e.ctrlKey || e.metaKey) && (e.key === 'a' || e.key === 'A')) {
    e.preventDefault();
    visibleRowPaths().forEach((p) => state.selection.add(p));
    renderExplorer();
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  switch (e.key) {
    case 'ArrowDown':
      e.preventDefault();
      moveCursor(1);
      break;
    case 'ArrowUp':
      e.preventDefault();
      moveCursor(-1);
      break;
    case 'Enter':
      if (state.cursorPath) {
        e.preventDefault();
        const item = state.items.find((it) => it.path === state.cursorPath);
        openItemRow(state.cursorPath, !item || item.isDir !== false);
      }
      break;
    case 'Backspace':
      e.preventDefault();
      goUp();
      break;
    case 'Delete':
      e.preventDefault();
      deleteSelection();
      break;
    default:
      break;
  }
}
// ---------- Explorer events ----------
function handleRowCheckbox(tr, event) {
  const rows = [...els.resultsBody.querySelectorAll('tr[data-path]')];
  const idx = rows.indexOf(tr);
  const path = tr.dataset.path;
  if (event.shiftKey && state.anchorIndex != null && state.anchorIndex >= 0) {
    const from = Math.min(state.anchorIndex, idx);
    const to = Math.max(state.anchorIndex, idx);
    for (let i = from; i <= to; i += 1) state.selection.add(rows[i].dataset.path);
  } else {
    if (state.selection.has(path)) state.selection.delete(path);
    else state.selection.add(path);
    state.anchorIndex = idx;
  }
  renderExplorer();
}

function bindTableEvents() {
  els.resultsBody.addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-path]');
    if (!tr) return;
    const path = tr.dataset.path;
    const isDir = tr.dataset.isdir === '1';

    if (e.target.matches('input[type="checkbox"]')) {
      handleRowCheckbox(tr, e);
      return;
    }
    if (e.target.closest('.badge-admin')) {
      toast('Protected system location — deletion requires confirmation and may need Administrator.', 'warning', 6000);
      return;
    }
    state.cursorPath = path;
    openItemRow(path, isDir);
  });

  els.resultsBody.addEventListener('contextmenu', (e) => {
    const tr = e.target.closest('tr[data-path]');
    if (!tr) return;
    e.preventDefault();
    state.cursorPath = tr.dataset.path;
    showContextMenu(e.clientX, e.clientY, tr.dataset.path);
  });

  els.chkSelectAll.addEventListener('change', () => {
    if (els.chkSelectAll.checked) {
      visibleRowPaths().forEach((p) => state.selection.add(p));
    } else {
      state.selection.clear();
    }
    state.anchorIndex = null;
    renderExplorer();
  });

  document.querySelectorAll('th[data-sort]').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (state.sortKey === key) {
        state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        state.sortKey = key;
        state.sortDir = (key === 'name' || key === 'path') ? 'asc' : 'desc';
      }
      persist();
      renderExplorer();
    });
  });
}

function bindContextMenu() {
  els.contextMenu.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const path = state.contextPath;
    hideContextMenu();
    if (!path) return;
    switch (btn.dataset.act) {
      case 'delete':
        requestDelete([path]);
        break;
      case 'copy':
        copyText(path);
        break;
      case 'properties':
        openProperties(path);
        break;
      default:
        break;
    }
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('#contextMenu')) hideContextMenu();
  });
  window.addEventListener('blur', hideContextMenu);
}

// ---------- Selection bar ----------
function bindSelectionBar() {
  els.btnDeleteSelected.addEventListener('click', deleteSelection);
  els.btnClearSelection.addEventListener('click', () => {
    state.selection.clear();
    state.anchorIndex = null;
    renderExplorer();
  });
}
// ---------- Toolbar bindings ----------
function bindToolbar() {
  els.btnBack.addEventListener('click', () => jumpHistory(-1));
  els.btnForward.addEventListener('click', () => jumpHistory(1));

  els.breadcrumb.addEventListener('click', (e) => {
    const crumb = e.target.closest('.crumb');
    if (crumb && crumb.dataset.path) navigate(crumb.dataset.path);
  });
  els.pathBar.addEventListener('click', startPathEdit);
  els.pathInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); endPathEdit(true); }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); endPathEdit(false); }
  });
  els.pathInput.addEventListener('blur', () => endPathEdit(false));

  els.searchInput.addEventListener('input', () => {
    state.search = els.searchInput.value;
    renderExplorer();
  });

  els.sortSelect.addEventListener('change', () => {
    state.sort = els.sortSelect.value;
    state.sortKey = state.sort;
    state.sortDir = state.sort === 'name' ? 'asc' : 'desc';
    persist();
    navigate(state.currentPath, { push: false });
  });

  els.chkLargeOnly.addEventListener('change', () => {
    state.filterLargeOnly = els.chkLargeOnly.checked;
    persist();
    navigate(state.currentPath, { push: false });
  });

  els.chkHideSystem.addEventListener('change', () => {
    state.filterHideSystem = els.chkHideSystem.checked;
    persist();
    renderExplorer();
  });
}

// ---------- Sidebar bindings ----------
function bindSidebar() {
  els.btnSidebar.addEventListener('click', toggleSidebar);
  els.sidebarOverlay.addEventListener('click', closeDrawer);

  els.sidebar.addEventListener('click', (e) => {
    const quick = e.target.closest('.nav-item[data-quick]');
    if (!quick) return;
    navigate(quickPath(quick.dataset.quick));
    if (isDrawerMode()) closeDrawer();
  });

  els.chkAutoScan.addEventListener('change', () => {
    state.autoScan = els.chkAutoScan.checked;
    persist();
    if (state.autoScan && state.mode === 'cleaner' && !state.cleanerData && !state.cleanerLoading) {
      runCleanerScan();
    }
  });

  els.chkNotify.addEventListener('change', async () => {
    state.notify = els.chkNotify.checked;
    persist();
    if (state.notify && typeof Notification !== 'undefined' && Notification.permission === 'default') {
      try { await Notification.requestPermission(); } catch (_) { /* denied/blocked */ }
    }
  });

  window.addEventListener('resize', () => {
    if (!isDrawerMode()) closeDrawer();
  });
}

// ---------- Mode tabs / cleaner bindings ----------
function bindMode() {
  els.tabExplorer.addEventListener('click', () => setMode('explorer'));
  els.tabCleaner.addEventListener('click', () => setMode('cleaner'));

  const startScan = () => runCleanerScan();
  els.btnScanC.addEventListener('click', startScan);
  els.btnScanCSidebar.addEventListener('click', () => {
    setMode('cleaner');
    runCleanerScan();
    if (isDrawerMode()) closeDrawer();
  });
  els.btnScanIdle.addEventListener('click', startScan);
  els.btnRescan.addEventListener('click', startScan);

  els.categoryGrid.addEventListener('click', (e) => {
    const actionBtn = e.target.closest('button[data-act]');
    if (actionBtn) {
      handleCleanerAction(actionBtn.dataset.act, actionBtn.dataset.path, actionBtn.dataset.name);
      return;
    }
    const pathEl = e.target.closest('[data-open-dir]');
    if (pathEl) {
      const p = pathEl.dataset.openDir;
      openFileModal(p, basename(p));
    }
  });
}

// ---------- File modal bindings ----------
function bindFileModal() {
  els.modalClose.addEventListener('click', closeFileModal);
  els.modalBack.addEventListener('click', modalBack);
  els.fileModal.addEventListener('click', (e) => {
    if (e.target === els.fileModal) closeFileModal();
    const dirEl = e.target.closest('[data-open-dir]');
    if (dirEl && els.modalContent.contains(dirEl)) {
      const p = dirEl.dataset.openDir;
      openFileModal(p, basename(p));
    }
  });
}
// ---------- Init ----------
async function init() {
  restore();

  // Reflect persisted state in the UI
  shell.classList.toggle('sidebar-collapsed', state.sidebarCollapsed);
  els.sortSelect.value = state.sort;
  els.chkLargeOnly.checked = state.filterLargeOnly;
  els.chkHideSystem.checked = state.filterHideSystem;
  els.chkAutoScan.checked = state.autoScan;
  els.chkNotify.checked = state.notify;
  setMode(state.mode, { persist: false });
  renderBreadcrumb();
  updateNavButtons();
  updateCleanerControls();

  // Bind everything
  bindTableEvents();
  bindContextMenu();
  bindSelectionBar();
  bindToolbar();
  bindSidebar();
  bindMode();
  bindFileModal();
  document.addEventListener('keydown', handleKeydown);

  // Parallel initial data
  loadUserHome();
  loadDrives();

  // Initial navigation (restored path, falling back to C:\ if it no longer exists)
  const ok = await navigate(state.currentPath, { push: false });
  if (!ok && state.currentPath !== DEFAULT_PATH) {
    await navigate(DEFAULT_PATH, { push: false });
  }
  // Note: no final setStatus('Ready') here — it would clobber the
  // "Loading sizes…" indicator while phase 2 is still running.
}

init();

