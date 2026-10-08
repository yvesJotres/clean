import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import shutil
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from folder_size import analyze_c_drive as analyze_c_drive_fast

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

FOLDER_SIZE_PATH = os.path.join(PROJECT_DIR, 'folder_size.py')
CONFIG_PATH = os.path.join(PROJECT_DIR, 'folder_size_config.py')

if os.path.exists(FOLDER_SIZE_PATH):
    try:
        import folder_size as fs
    except ImportError:
        fs = None
else:
    fs = None

if not os.path.exists(CONFIG_PATH):
    CONFIG_PATH = None


def format_size(value):
    if fs is not None:
        return fs.format_size(value)
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def debug_log(message):
    print(f"[debug] {message}")


def _fallback_folder_size(root):
    """Recursive size/count used only when folder_size.py is unavailable."""
    total = 0
    count = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, filename))
            except OSError:
                continue
            count += 1
    return total, count


def scan_folder(path, sort_by='size', filter_large_only=False, mode='detail'):
    entries = []
    fast = mode == 'fast'
    debug_log(f"scan_folder start path={path} sort={sort_by} filter_large_only={filter_large_only} mode={mode}")
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    junk = bool(fs.is_junk(entry.name)) if fs is not None else False
                    if entry.is_dir(follow_symlinks=False):
                        if fast:
                            # Fast-list mode: no size recursion, sizes arrive later
                            # via /api/get-sizes.
                            entries.append({
                                'name': entry.name,
                                'size': None,
                                'files': None,
                                'path': entry.path,
                                'isDir': True,
                                'junk': junk,
                            })
                            continue
                        size, count = fs.folder_size(entry.path, progress=False) if fs is not None else (0, 0)
                        if size == 0 and count == 0:
                            continue
                        entries.append({
                            'name': entry.name,
                            'size': size,
                            'files': count,
                            'path': entry.path,
                            'isDir': True,
                            'junk': junk,
                        })
                    elif entry.is_file(follow_symlinks=False):
                        # Files: in fast mode, size=None (filled later via get-sizes);
                        # in detail mode, use actual file size.
                        if fast:
                            entries.append({
                                'name': entry.name,
                                'size': None,
                                'files': None,
                                'path': entry.path,
                                'isDir': False,
                                'junk': junk,
                            })
                        else:
                            try:
                                stat = entry.stat(follow_symlinks=False)
                                entries.append({
                                    'name': entry.name,
                                    'size': stat.st_size,
                                    'files': 1,
                                    'path': entry.path,
                                    'isDir': False,
                                    'junk': junk,
                                })
                            except OSError:
                                # Unreadable file - show with null size
                                entries.append({
                                    'name': entry.name,
                                    'size': None,
                                    'files': None,
                                    'path': entry.path,
                                    'isDir': False,
                                    'junk': junk,
                                })
                except Exception as exc:
                    debug_log(f"scan_folder entry failed name={entry.name} error={exc}")
                    continue
    except OSError as exc:
        debug_log(f"scan_folder OSError path={path} error={exc}")
        return []

    debug_log(f"scan_folder complete path={path} entries={len(entries)}")

    if filter_large_only and not fast:
        # For files: use their own size; for folders: use recursive total
        entries = [item for item in entries if (item.get('size') or 0) >= 1024 * 1024]

    if fast or sort_by == 'name':
        entries.sort(key=lambda item: item['name'].lower())
    else:
        entries.sort(key=lambda item: (item.get('size') or 0), reverse=True)

    return entries


def scan_folder_live(path, sort_by='size', filter_large_only=False, on_progress=None):
    entries = []
    debug_log(f"scan_folder_live start path={path} sort={sort_by} filter_large_only={filter_large_only}")
    try:
        with os.scandir(path) as it:
            items = list(it)
    except OSError as exc:
        debug_log(f"scan_folder_live OSError path={path} error={exc}")
        return []

    def finalize_entries(current):
        filtered = current
        if filter_large_only:
            filtered = [item for item in current if item['size'] >= 1024 * 1024]
        if sort_by == 'name':
            filtered.sort(key=lambda item: item['name'].lower())
        else:
            filtered.sort(key=lambda item: item['size'], reverse=True)
        return filtered

    total = len(items)
    debug_log(f"scan_folder_live enumerating {total} top-level entries")
    for index, entry in enumerate(items):
        if entry.is_dir(follow_symlinks=False):
            try:
                size, count = fs.folder_size(entry.path, progress=False) if fs is not None else (0, 0)
                if size == 0 and count == 0:
                    continue
                junk = bool(fs.is_junk(entry.name)) if fs is not None else False
                entries.append({
                    'name': entry.name,
                    'size': size,
                    'files': count,
                    'path': entry.path,
                    'junk': junk,
                })
                visible_entries = finalize_entries(entries)
                debug_log(f"scan_folder_live folder processed name={entry.name} size={size} count={count} visible={len(visible_entries)}")
                if on_progress:
                    on_progress({
                        'current': entry.path,
                        'processed': index + 1,
                        'total': total,
                        'count': len(visible_entries),
                        'status': f"Scanning {entry.name}...",
                        'items': visible_entries,
                    })
            except Exception as exc:
                debug_log(f"scan_folder_live entry failed name={entry.name} error={exc}")
                continue

    final_entries = finalize_entries(entries)
    debug_log(f"scan_folder_live complete path={path} final_entries={len(final_entries)}")
    return final_entries


def drive_info(drive):
    if fs is not None:
        try:
            label, used, total, free = fs.drive_info(drive)
            return {
                'drive': drive,
                'label': label,
                'used': used,
                'total': total,
                'free': free,
            }
        except Exception:
            pass

    total = 0
    free = 0
    try:
        usage = shutil.disk_usage(drive)
        total = usage.total
        free = usage.free
    except Exception:
        pass
    return {
        'drive': drive,
        'label': 'Local Disk',
        'used': max(0, total - free),
        'total': total,
        'free': free,
    }


def get_drives():
    if fs is not None:
        try:
            drives = fs.get_drives()
            return [drive_info(d) for d in drives]
        except Exception:
            pass

    drives = []
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        path = f"{letter}:\\"
        if os.path.exists(path):
            drives.append(drive_info(path))
    return drives


def is_protected(path, name):
    if fs is not None:
        return fs.is_protected(path, name)
    lowered = name.lower()
    protected_names = {'windows', 'program files', 'program files (x86)', 'programdata', 'users'}
    protected_paths = {
        'c:\\windows', 'c:\\program files', 'c:\\program files (x86)', 'c:\\programdata', 'c:\\users'
    }
    return lowered in protected_names or path.lower() in protected_paths or os.path.splitdrive(path)[1] in ('\\', '/')


JUNK_NAMES = {
    'temp', 'tmp', 'cache', 'logs', 'log', 'crashdumps', 'minidump',
    'recycle.bin', 'system volume information', 'windows.old',
    'prefetch', 'deliveryoptimization', 'branchcache',
    'amd', 'nvidia', 'intel', 'package cache',
    'chrome', 'firefox', 'edge', 'opera', 'browser',
    'spotify', 'discord', 'slack', 'teams', 'zoom',
    'steam', 'epic', 'origin', 'uplay', 'battlenet',
    'thumbnails', '.thumbnails', 'trash', '.trash',
    'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache',
    'dist', 'build', 'target', 'bin', 'obj',
    'vendor', 'packages', '.nuget', 'gradle', '.gradle',
}

BLOAT_PATTERNS = {
    'nvidia': ('cache', 'NVIDIA driver logs/cache', 'Run DDU, reinstall minimal driver'),
    'nvidia corporation': ('cache', 'NVIDIA driver logs/cache', 'Run DDU, reinstall minimal driver'),
    'intel package cache': ('cache', 'Intel installer leftovers', 'Safe to delete after install'),
    'package cache': ('cache', 'Windows installer cache (MSI)', 'Safe to delete - Windows recreates'),
    'chocolatey': ('cache', 'Chocolatey package cache', 'Safe to delete'),
    'chocolateyhttpcache': ('cache', 'Chocolatey package cache', 'Safe to delete'),
    'epictemp': ('cache', 'Epic installer temp', 'Safe to delete'),
    'epicinstallertemp': ('cache', 'Epic installer temp', 'Safe to delete'),
    'installer': ('cache', 'Generic installer temp', 'Safe to delete if not installing'),
    'log': ('cache', 'Application logs', 'Safe to delete'),
    'logs': ('cache', 'Application logs', 'Safe to delete'),
    'crashdump': ('cache', 'Crash dumps', 'Safe to delete'),
    'minidump': ('cache', 'Minidump files', 'Safe to delete'),
    'temp': ('cache', 'Temporary files', 'Safe to delete'),
    'corsair': ('move', 'Corsair iCUE software', 'Uninstall, reinstall to D:'),
    'icue': ('move', 'Corsair iCUE software', 'Uninstall, reinstall to D:'),
    'visual studio': ('move', 'Visual Studio', 'VS Installer -> Move install location'),
    'microsoft visual studio': ('move', 'Visual Studio', 'VS Installer -> Move install location'),
    'postgresql': ('move', 'PostgreSQL database', 'Move data dir or reinstall to D:'),
    'postgres': ('move', 'PostgreSQL database', 'Move data dir or reinstall to D:'),
    'metaquotes': ('decide', 'MetaTrader 5 data', 'Keep if trading, else uninstall MT5'),
    'metatrader': ('decide', 'MetaTrader data', 'Keep if trading, else uninstall'),
    'overwolf': ('decide', 'Overwolf overlay/apps', 'Uninstall if unused'),
    'razersynapse': ('decide', 'Razer Synapse', 'Uninstall if unused'),
    'razer': ('decide', 'Razer software', 'Uninstall if unused'),
    'steelseries': ('decide', 'SteelSeries Engine', 'Uninstall if unused'),
    'logitech': ('decide', 'Logitech G Hub', 'Uninstall if unused'),
    'lghub': ('decide', 'Logitech G Hub', 'Uninstall if unused'),
}

def analyze_c_drive_bloat():
    """Analyze C: drive folders for known bloat patterns and return categorized results."""
    targets = [
        r"C:\ProgramData",
        r"C:\Program Files",
        r"C:\Program Files (x86)",
    ]

    all_folders = []
    for target in targets:
        if os.path.isdir(target):
            debug_log(f"analyze_c_drive scanning {target}")
            fi, _ = fs.scan(target, "size", False) if fs is not None else ([], [])
            all_folders.extend(fi)

    delete_items = []
    move_items = []
    decide_items = []

    for sz, cnt, path, name, _ in all_folders:
        lname = name.lower()
        lpath = path.lower()

        matched = False
        for pattern, (category, desc, action) in BLOAT_PATTERNS.items():
            if pattern in lname or pattern in lpath:
                item = {'name': name, 'size': sz, 'files': cnt, 'path': path, 'desc': desc, 'action': action}
                if category == "cache":
                    delete_items.append(item)
                elif category == "move":
                    move_items.append(item)
                elif category == "decide":
                    decide_items.append(item)
                matched = True
                break

    delete_items.sort(key=lambda x: x['size'], reverse=True)
    move_items.sort(key=lambda x: x['size'], reverse=True)
    decide_items.sort(key=lambda x: x['size'], reverse=True)

    return {'delete': delete_items, 'move': move_items, 'decide': decide_items}


class WebHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT_DIR, **kwargs)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/api/get-sizes':
            try:
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length).decode('utf-8') or '{}'
                payload = json.loads(body)
                paths = payload.get('paths') or []
                if not isinstance(paths, list):
                    raise ValueError('paths must be a list')
            except (ValueError, UnicodeDecodeError) as exc:
                self._send_json({'error': f'Invalid request body: {exc}'}, 400)
                return

            paths = [str(p) for p in paths][:1000]
            debug_log(f"get-sizes count={len(paths)}")

            def measure(target):
                if not os.path.isdir(target):
                    return None
                try:
                    if fs is not None:
                        size, count = fs.folder_size(target, progress=False)
                    else:
                        size, count = _fallback_folder_size(target)
                    return target, size, count
                except Exception as exc:
                    debug_log(f"get-sizes failed path={target} error={exc}")
                    return target, 0, 0

            sizes = {}
            counts = {}
            if paths:
                workers = min(16, max(1, len(paths)))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    for result in pool.map(measure, paths):
                        if result is None:
                            continue
                        target, size, count = result
                        sizes[target] = size
                        counts[target] = count
            self._send_json({'sizes': sizes, 'counts': counts})
            return

        self._send_json({'error': 'Not Found'}, 404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == '/api/drives':
            data = {'drives': get_drives()}
            self._send_json(data)
            return

        if path == '/api/user-home':
            self._send_json({'home': os.path.expanduser('~')})
            return

        if path == '/api/scan':
            query = urllib.parse.parse_qs(parsed.query)
            target = query.get('path', [''])[0]
            sort_by = query.get('sort', ['size'])[0]
            filter_large_only = query.get('filter', ['0'])[0] == '1'
            mode = query.get('mode', ['detail'])[0]
            if query.get('fast', ['0'])[0] == '1':
                mode = 'fast'
            if mode not in ('fast', 'detail'):
                mode = 'detail'

            if not target or not os.path.isdir(target):
                self._send_json({'error': 'Invalid folder path.'}, 400)
                return

            items = scan_folder(target, sort_by=sort_by, filter_large_only=filter_large_only, mode=mode)
            total = sum(item['size'] or 0 for item in items)
            known = [item for item in items if item['size'] is not None]
            largest = max(known, key=lambda item: item['size'], default=None)
            stats = {
                'path': target,
                'folder_count': len(items),
                'total_size': total,
                'largest': largest,
            }
            self._send_json({'items': items, 'stats': stats})
            return

        if path == '/api/scan-live':
            query = urllib.parse.parse_qs(parsed.query)
            target = query.get('path', [''])[0]
            sort_by = query.get('sort', ['size'])[0]
            filter_large_only = query.get('filter', ['0'])[0] == '1'

            debug_log(f"scan-live request path={target} sort={sort_by} filter_large_only={filter_large_only}")

            if not target or not os.path.isdir(target):
                debug_log(f"scan-live invalid path={target}")
                self._send_json({'error': 'Invalid folder path.'}, 400)
                return

            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache, no-transform')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()

            def emit(event_name, payload):
                encoded = json.dumps(payload).encode('utf-8')
                self.wfile.write(f"event: {event_name}\ndata: {encoded.decode('utf-8')}\n\n".encode('utf-8'))
                self.wfile.flush()

            def on_progress(payload):
                debug_log(f"scan-live update path={target} processed={payload.get('processed')}/{payload.get('total')} current={payload.get('current')} count={payload.get('count')}")
                emit('scan_update', payload)

            emit('scan_started', {'path': target})
            try:
                items = scan_folder_live(target, sort_by=sort_by, filter_large_only=filter_large_only, on_progress=on_progress)
                total = sum(item['size'] for item in items)
                largest = max(items, key=lambda item: item['size'], default=None)
                debug_log(f"scan-live complete path={target} items={len(items)} total_size={total}")
                emit('scan_complete', {
                    'path': target,
                    'items': items,
                    'stats': {
                        'folder_count': len(items),
                        'total_size': total,
                        'largest': largest,
                    },
                })
            except Exception as exc:
                debug_log(f"scan-live exception path={target} error={exc}")
                emit('scan_error', {'error': str(exc)})
            return

        if path == '/api/open':
            query = urllib.parse.parse_qs(parsed.query)
            target = query.get('path', [''])[0]
            if not target or not os.path.isdir(target):
                self._send_json({'error': 'Invalid folder path.'}, 400)
                return

            try:
                if hasattr(os, 'startfile'):
                    os.startfile(target)
                else:
                    subprocess.Popen(['xdg-open', target])
                self._send_json({'ok': True, 'message': f'Opened {target}'})
            except Exception as exc:
                self._send_json({'error': str(exc)}, 500)
            return

        if path == '/api/delete':
            query = urllib.parse.parse_qs(parsed.query)
            target = query.get('path', [''])[0]
            if not target or not os.path.exists(target):
                self._send_json({'error': 'Invalid folder path.'}, 400)
                return

            name = os.path.basename(target)
            if is_protected(target, name):
                self._send_json({'error': 'This folder is protected and cannot be deleted.'}, 403)
                return

            try:
                shutil.rmtree(target)
                self._send_json({'ok': True, 'message': f'Deleted: {target}'})
            except Exception as exc:
                self._send_json({'error': str(exc)}, 500)
            return

        if path == '/api/analyze-c':
                    cache_items, move_items, decide_items = fs.analyze_c_drive()
            
                    def to_dict(items):
                        return [{'name': i[3], 'size': i[0], 'files': i[1], 'path': i[2], 'desc': i[4], 'action': i[5]} for i in items]
            
                    items = {
                        'cache': to_dict(cache_items),
                        'move': to_dict(move_items),
                        'decide': to_dict(decide_items)
                    }
                    summary = {
                        'cache': sum(i['size'] for i in items['cache']),
                        'move': sum(i['size'] for i in items['move']),
                        'decide': sum(i['size'] for i in items['decide']),
                        'cacheCount': len(items['cache']),
                        'moveCount': len(items['move']),
                        'decideCount': len(items['decide']),
                    }
                    self._send_json({'summary': summary, 'cache': items['cache'], 'move': items['move'], 'decide': items['decide']})
                    return

        if path == '/' or path == '/index.html':
            self.path = '/index.html'
            return super().do_GET()
        if path == '/cleaner.html':
            self.path = '/cleaner.html'
            return super().do_GET()

        if path.startswith('/css/') or path.startswith('/js/'):
            return super().do_GET()

        self.send_error(404, 'Not Found')

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


if __name__ == '__main__':
    port = 8000
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])

    server = ThreadingHTTPServer(('127.0.0.1', port), WebHandler)
    print(f'Folder Size Explorer web UI is running at http://127.0.0.1:{port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down web UI...')
        server.server_close()
