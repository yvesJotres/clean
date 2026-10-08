const state = {
  selectedPath: '',
  selectedRow: null,
  items: [],
  drives: [],
  rootPath: '',
  liveScan: null,
  scanning: false,
  sortKey: 'size',
  sortDir: 'desc',
};

const els = {
  driveList: document.getElementById('driveList'),
  folderInput: document.getElementById('folderInput'),
  sortSelect: document.getElementById('sortSelect'),
  filterLargeOnly: document.getElementById('filterLargeOnly'),
  searchInput: document.getElementById('searchInput'),
  statusText: document.getElementById('statusText'),
  progressBar: document.getElementById('progressBar'),
  progressText: document.getElementById('progressText'),
  resultsProgressBar: document.getElementById('resultsProgressBar'),
  resultsBody: document.getElementById('resultsBody'),
  statFolders: document.getElementById('statFolders'),
  statTotal: document.getElementById('statTotal'),
  statLargest: document.getElementById('statLargest'),
  statJunk: document.getElementById('statJunk'),
  btnScan: document.getElementById('btnScan'),
  btnOpen: document.getElementById('btnOpen'),
  btnDelete: document.getElementById('btnDelete'),
};

function setProgress(percent) {
  if (els.progressBar) els.progressBar.style.width = `${percent}%`;
  if (els.progressText) els.progressText.textContent = `${percent}%`;
  if (els.resultsProgressBar) els.resultsProgressBar.style.width = `${percent}%`;
}

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

function setStatus(message) {
  if (els.statusText) els.statusText.textContent = message;
}

function updateScanButton() {
  if (!els.btnScan) return;
  if (state.scanning) {
    els.btnScan.textContent = 'Cancel Scan';
    els.btnScan.classList.add('loading');
  } else {
    els.btnScan.textContent = 'Scan folder';
    els.btnScan.classList.remove('loading');
  }
}

function updateActionButtons() {
  const hasSelection = Boolean(state.selectedPath);
  if (els.btnOpen) els.btnOpen.disabled = !hasSelection;
  if (els.btnDelete) els.btnDelete.disabled = !hasSelection;
}

function getSelectedSort() {
  return els.sortSelect.value;
}

function getFilteredItems() {
  const term = els.searchInput.value.trim().toLowerCase();
  return state.items.filter((item) => {
    const matchesText = !term || item.name.toLowerCase().includes(term) || item.path.toLowerCase().includes(term);
    return matchesText;
  });
}

function sortRows(rows) {
  const sorted = [...rows];
  sorted.sort((a, b) => {
    let aValue = a[state.sortKey];
    let bValue = b[state.sortKey];

    if (state.sortKey === 'name' || state.sortKey === 'path') {
      aValue = String(aValue || '').toLowerCase();
      bValue = String(bValue || '').toLowerCase();
    }

    if (state.sortKey === 'size' || state.sortKey === 'files') {
      aValue = Number(aValue || 0);
      bValue = Number(bValue || 0);
    }

    if (state.sortKey === 'junk') {
      aValue = a.junk ? 1 : 0;
      bValue = b.junk ? 1 : 0;
    }

    if (aValue < bValue) return state.sortDir === 'asc' ? -1 : 1;
    if (aValue > bValue) return state.sortDir === 'asc' ? 1 : -1;
    return 0;
  });

  return sorted;
}

function renderTable() {
  if (!els.resultsBody) return;
  const rows = sortRows(getFilteredItems());
  els.resultsBody.innerHTML = '';

  if (!rows.length) {
    let emptyMessage = 'No folders found.';

    if (state.scanning) {
      emptyMessage = 'Scanning folder contents...';
    } else if (state.items.length) {
      emptyMessage = 'No folders match your search.';
    } else {
      emptyMessage = 'No folders scanned yet. Select a drive and click Scan folder.';
    }

    els.resultsBody.innerHTML = `<tr class="empty-row"><td colspan="5" class="empty-state">${emptyMessage}</td></tr>`;
    updateActionButtons();
    return;
  }

  rows.forEach((item) => {
    const row = document.createElement('tr');
    if (state.selectedRow === item.path) row.classList.add('selected');

    row.innerHTML = `
      <td class="name-cell">${item.name}</td>
      <td class="size-cell">${formatSize(item.size)}</td>
      <td>${Number(item.files).toLocaleString()}</td>
      <td class="path-cell">${item.path}</td>
      <td>${item.junk ? '<span class="junk-badge">Junk</span>' : '—'}</td>
    `;

    row.addEventListener('click', () => {
      const isSelected = state.selectedRow === item.path;
      state.selectedRow = isSelected ? null : item.path;
      state.selectedPath = isSelected ? '' : item.path;
      renderTable();
      updateActionButtons();
    });

    row.dataset.path = item.path;
    els.resultsBody.appendChild(row);
  });

  updateActionButtons();
}

function renderStats() {
  if (!els.statFolders || !els.statTotal || !els.statLargest || !els.statJunk) return;
  const rows = getFilteredItems();
  const total = rows.reduce((sum, item) => sum + item.size, 0);
  const largest = rows.reduce((big, item) => (item.size > (big?.size || -1) ? item : big), null);
  const junkCount = rows.filter((item) => item.junk).length;

  els.statFolders.textContent = String(rows.length);
  els.statTotal.textContent = formatSize(total);
  els.statLargest.textContent = largest ? `${largest.name} (${formatSize(largest.size)})` : '—';
  els.statJunk.textContent = String(junkCount);
}

function renderDrives(drives) {
  state.drives = drives;
  if (!els.driveList) return;
  els.driveList.innerHTML = '';

  drives.forEach((drive) => {
    const wrapper = document.createElement('button');
    wrapper.type = 'button';
    wrapper.className = 'drive-item';
    if (drive.drive === state.rootPath) wrapper.classList.add('active');

    const used = Number(drive.used || 0);
    const total = Number(drive.total || 1);
    const percent = Math.min(100, Math.max(0, (used / total) * 100));
    const usedGB = used / 1024 / 1024 / 1024;
    const totalGB = total / 1024 / 1024 / 1024;

    wrapper.innerHTML = `
      <div class="drive-meta">
        <div class="drive-name">${drive.drive}</div>
        <div class="drive-size">${drive.label}</div>
      </div>
      <div class="drive-bar-wrap">
        <div class="drive-bar"><div class="drive-bar-fill" style="width:${percent}%"></div></div>
        <div class="drive-bar-meta">${percent.toFixed(0)}% • ${usedGB.toFixed(1)} GB / ${totalGB.toFixed(1)} GB</div>
      </div>
    `;

    wrapper.addEventListener('click', () => {
      state.rootPath = drive.drive;
      els.folderInput.value = drive.drive;
      renderDrives(drives);
      scanFolder(drive.drive);
    });

    els.driveList.appendChild(wrapper);
  });
}

async function fetchJson(url) {
  const response = await fetch(url);
  const json = await response.json();
  if (!response.ok) {
    throw new Error(json.error || 'Request failed');
  }
  return json;
}

function cancelActiveScan() {
  if (state.liveScan) {
    state.liveScan.close();
    state.liveScan = null;
  }
  state.scanning = false;
  if (els.resultsProgressBar) els.resultsProgressBar.style.width = '0%';
  updateScanButton();
  setStatus('Scan cancelled');
}

async function loadDrives() {
  try {
    const data = await fetchJson('/api/drives');
    const drives = data.drives || [];
    renderDrives(drives);
    if (drives.length) {
      const first = drives[0].drive;
      state.rootPath = first;
      els.folderInput.value = first;
      await scanFolder(first);
    }
  } catch (error) {
    setStatus(error.message);
  }
}

async function scanFolder(path) {
  if (state.scanning && state.liveScan) {
    cancelActiveScan();
    return;
  }

  state.rootPath = path;
  state.scanning = true;
  if (els.folderInput) els.folderInput.value = path;
  setProgress(0);
  renderTable();
  updateScanButton();
  setStatus(`Scanning ${path}...`);

  const params = new URLSearchParams({
    path,
    sort: getSelectedSort(),
    filter: els.filterLargeOnly.checked ? '1' : '0',
  });

  const stream = new EventSource(`/api/scan-live?${params.toString()}`);
  state.liveScan = stream;

  stream.addEventListener('scan_started', () => {
    setStatus(`Starting scan for ${path}...`);
  });

  stream.addEventListener('scan_update', (event) => {
    const payload = JSON.parse(event.data);
    const total = Math.max(1, Number(payload.total) || 1);
    const processed = Math.min(total, Number(payload.processed) || 0);
    const percent = Math.round((processed / total) * 100);
    setProgress(percent);

    if (Array.isArray(payload.items)) {
      state.items = payload.items;
      renderTable();
      renderStats();
    }

    const label = payload.status || `Scanning ${path}...`;
    setStatus(label);
  });

  stream.addEventListener('scan_complete', (event) => {
    const payload = JSON.parse(event.data);
    state.items = payload.items || [];
    state.scanning = false;
    state.liveScan = null;
    renderTable();
    renderStats();
    setStatus(`Found ${state.items.length} folder(s) in ${path}`);
    setProgress(100);
    updateScanButton();
    stream.close();
  });

  stream.addEventListener('scan_error', (event) => {
    const payload = JSON.parse(event.data);
    state.scanning = false;
    state.liveScan = null;
    if (els.resultsProgressBar) els.resultsProgressBar.style.width = '0%';
    updateScanButton();
    setStatus(payload.error || 'Scan failed');
    stream.close();
  });
}

async function openSelected() {
  if (!state.selectedPath) {
    setStatus('Select a folder first.');
    return;
  }

  try {
    const params = new URLSearchParams({ path: state.selectedPath });
    await fetchJson(`/api/open?${params.toString()}`);
    setStatus(`Opening ${state.selectedPath}`);
  } catch (error) {
    setStatus(error.message);
  }
}

async function deleteSelected() {
  if (!state.selectedPath) {
    setStatus('Select a folder first.');
    return;
  }

  const selectedItem = state.items.find((item) => item.path === state.selectedPath) || null;
  const folderName = selectedItem ? selectedItem.name : state.selectedPath.split(/[/\\]/).pop();
  const folderSize = selectedItem ? formatSize(selectedItem.size) : 'unknown';
  const confirmed = window.confirm(`Delete selected folder?\n\n${folderName}\n${state.selectedPath}\n\nSize: ${folderSize}`);
  if (!confirmed) return;

  try {
    const params = new URLSearchParams({ path: state.selectedPath });
    const data = await fetchJson(`/api/delete?${params.toString()}`);
    state.items = state.items.filter((item) => item.path !== state.selectedPath);
    state.selectedPath = '';
    state.selectedRow = null;
    renderTable();
    renderStats();
    setStatus(data.message || 'Folder deleted.');
  } catch (error) {
    setStatus(error.message);
  }
}

function bindEvents() {
  if (els.btnScan) els.btnScan.addEventListener('click', () => {
    const target = els.folderInput.value.trim();
    if (state.scanning) {
      cancelActiveScan();
      return;
    }
    if (target) scanFolder(target);
  });

  const btnBrowse = document.getElementById('btnBrowse');
  if (btnBrowse) btnBrowse.addEventListener('click', async () => {
    const value = window.prompt('Enter a folder path to scan:', els.folderInput.value || 'C:\\');
    if (value && value.trim()) {
      const path = value.trim();
      state.rootPath = path;
      if (els.folderInput) els.folderInput.value = path;
      await scanFolder(path);
    }
  });

  if (els.sortSelect) els.sortSelect.addEventListener('change', () => {
    if (state.rootPath) scanFolder(state.rootPath);
  });

  if (els.filterLargeOnly) els.filterLargeOnly.addEventListener('change', () => {
    if (state.rootPath) scanFolder(state.rootPath);
  });

  if (els.searchInput) els.searchInput.addEventListener('input', () => {
    renderTable();
    renderStats();
  });

  document.querySelectorAll('th[data-sort]').forEach((header) => {
    header.addEventListener('click', () => {
      const key = header.dataset.sort;
      if (state.sortKey === key) {
        state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        state.sortKey = key;
        state.sortDir = key === 'name' || key === 'path' ? 'asc' : 'desc';
      }
      renderTable();
      renderStats();
    });
  });

  if (els.btnOpen) els.btnOpen.addEventListener('click', openSelected);
  if (els.btnDelete) els.btnDelete.addEventListener('click', deleteSelected);
}

bindEvents();
updateScanButton();
updateActionButtons();
loadDrives();
