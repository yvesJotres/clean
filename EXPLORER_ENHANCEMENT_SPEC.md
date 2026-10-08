# Explorer Enhancement Specification: Progressive Sizes + File Visibility

## 1. Progressive Size / File-Count Accumulation
**Goal**: The user should *see* the size and file-count numbers climb in real-time as the background workers finish, providing a clear sense of progress and control.

### Implementation
| Aspect | Detail |
|--------|--------|
| **Server** | `POST /api/get-sizes` already returns `{sizes: {path: n}, counts: {path: n}}`. Keep this contract. |
| **Client (`unified.js`)** | - After `mode=fast` render, immediately start the background size fetch. <br>- **Do not wait for the full batch**. As each worker resolves (or as the JSON stream/chunks arrive), call `patchSizeCells(path, size, count)` to incrementally update the two `<td class="c-size">` and `<td class="c-files">` cells. <br>- Keep the status-bar text dynamic: `“Loading sizes… 12/47 folders resolved”`. |
| **UI Feedback** | - Size cell: show a tiny spinner `⟳` next to the number while that row is still pending; replace with final value when done. <br>- File-count cell: same treatment. <br>- Progress bar in status-bar reflects `resolved / totalVisible`. |
| **Cancel / Re-navigate** | Abort in-flight `requestId` (already implemented) so stale updates never paint over a new folder. |

---

## 2. Show Files in Explorer (Not Just Folders)
**Goal**: The Explorer table must list **both folders and files** so the user can browse, select, and delete individual files.

### Data Contract Changes (`/api/scan`)
| Parameter | Current | New |
|-----------|---------|-----|
| `mode=fast` | Returns only directories (`isDir=true`) | Return **all entries**: `{name, path, isDir, size: null, files: null, junk, ext}` |
| `mode=detail` | Returns directories with computed sizes | Return **all entries** with computed sizes. Files get their actual `size`; folders get recursive totals. |
| Response shape | `items: [{…}]` | Unchanged — just includes `isDir: false` rows. |

### Client Rendering (`unified.js`)
1. **Table rows**: Render every item. Files get a 📄 icon; folders keep 📁.
2. **Click behavior**:
   - Folder row → `navigate(path)` (pushes history).
   - File row → `openWithSystem(path)` (opens in default app).
3. **Delete**: Works identically for files and folders (calls `/api/delete`).
4. **Sort**: 
   - `size` sort: files by their size, folders by recursive total (once resolved).
   - `name` sort: interleaved alphanumeric.
5. **“≥ 1 MB” filter**: Applies to *both* files and folders (files use their own size; folders use recursive total).
6. **Search**: Filters file names and folder names equally.
7. **Context menu / selection bar**: Identical for files and folders.
8. **Keyboard**: `Enter` on file → open; `Enter` on folder → navigate.

### Visual Distinction
- File rows: slightly lighter text color (`var(--muted-strong)` vs `var(--text)` for folders) or an `is-file` class for CSS targeting.
- File size column shows actual bytes (no placeholder — files are instant in `detail` mode; in `fast` mode they can be fetched alongside folders via the same `get-sizes` endpoint if desired, or just show “—” until user switches to detail view).

---

## 3. Acceptance Criteria
| # | Scenario | Expected |
|---|----------|----------|
| 1 | Open a folder with 500 items | Table renders instantly (fast mode) showing all files + folders; Size/Files columns show `—` with spinners. |
| 2 | Wait 2 s | Numbers start ticking up row-by-row; status bar shows “Loading sizes… 127/500”. |
| 3 | All resolved | Spinners gone; final sizes/counts displayed; status “Sizes loaded for 500 items”. |
| 4 | Click a `.txt` file | Opens in Notepad (or default editor). |
| 5 | Click a subfolder | Navigates into it; history works (Back button). |
| 6 | Right-click a file → Delete | Confirmation dialog → `/api/delete` → row removed optimistically. |
| 7 | Toggle “≥ 1 MB” | Rows < 1 MB hidden (files by own size, folders by recursive total once known). |
| 8 | Search “log” | Both `error.log` (file) and `logs/` (folder) remain visible. |
| 9 | Sort by Size | Files and folders interleaved by size (folders use resolved totals). |
| 10 | Re-navigate while loading | In-flight requests cancelled; no UI flicker or wrong numbers. |

---

## 4. Files to Modify
| File | Change |
|------|--------|
| `webui/server.py` | Update `/api/scan` to return files in `fast` mode; ensure `detail` mode includes files with correct sizes; keep `/api/get-sizes` unchanged (it already accepts any paths). |
| `webui/js/unified.js` | - `navigate()`: no logic change (already calls `mode=fast`). <br>- `renderExplorer()`: iterate `state.items` (now includes files); render icon, click-handler branches on `isDir`. <br>- `fetchSizesForRows()`: send **all visible paths** (files + folders); patch both `c-size` and `c-files` per row. <br>- `getVisibleItems()`: filter/search/sort work on mixed list. <br>- CSS: add `.is-file` row styling if desired. |
| `webui/css/unified.css` | Optional: `.row.is-file .c-name { color: var(--muted-strong); }` and ensure mobile card layout handles file rows. |

---

## 5. Quick-Start for Agent
```bash
# 1. Apply server changes
#    - Edit webui/server.py do_GET /api/scan → include files when mode=fast
# 2. Apply client changes
#    - Edit webui/js/unified.js per sections above
# 3. Restart server
python webui/server.py
# 4. Test at http://127.0.0.1:8000/
```

---

## 6. Notes / Edge Cases
- **Symlinks / Junctions**: Server should skip or mark `isDir` conservatively; client treats them as folders.
- **Permission errors**: Server returns `size: null, files: null` for unreadable entries; client shows “—” with a tooltip “Access denied”.
- **Huge folders (10k+ entries)**: `mode=fast` is still instant; `get-sizes` batches at 1,000 paths/request — client should page requests if visible rows > 1,000 (unlikely with search/filter).
- **Memory**: `state.items` now holds files + folders; acceptable (< few MB for typical directories).

---

**End of spec** — hand this to the agent.