#!/usr/bin/env python3
"""Smart interactive folder size explorer for Windows."""

import os, sys, argparse, string, ctypes, threading, time, shutil, json
# import winshell

from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:
    tk = None
    ttk = None
    filedialog = None
    messagebox = None
try:
    import folder_size_config as _cfg
except ImportError:
    _cfg = None

def _c(name, default):
    return getattr(_cfg, name, default) if _cfg else default

# ── ANSI colour support ────────────────────────────────────────────────────────
_k32 = ctypes.windll.kernel32
try:
    _h = _k32.GetStdHandle(-11)
    _m = ctypes.c_ulong()
    _k32.GetConsoleMode(_h, ctypes.byref(_m))
    _k32.SetConsoleMode(_h, _m.value | 0x0004)
except Exception:
    pass

R="\033[91m"; Y="\033[93m"; G="\033[92m"; C="\033[96m"
W="\033[97m"; DIM="\033[90m"; MG="\033[95m"; BD="\033[1m"; RS="\033[0m"

_out_lock = threading.Lock()

JUNK_NAMES      = {s.lower() for s in _c("JUNK_NAMES", {
    "temp","$recycle.bin","cache","tmp","crashdumps","squirreltemp",
    "pip","npm-cache","pypa","__pycache__",".cache",
    "lm-studio-updater","curseforge-updater","prefetch",
})}

# Known bloat patterns for C: drive analysis
BLOAT_PATTERNS = {
    # (name_pattern, category, description, action)
    # NVIDIA - specific subfolders only, NOT entire NVIDIA Corporation
    "nvidia corporation\\installerstaging": ("cache", "NVIDIA driver installer staging", "Safe to delete after driver install"),
    "nvidia corporation\\netservice": ("cache", "NVIDIA NetService logs", "Safe to delete"),
    "nvidia corporation\\shadowplay": ("cache", "NVIDIA ShadowPlay recordings/cache", "Disable ShadowPlay or clear folder"),
    "nvidia corporation\\umdlogs": ("cache", "NVIDIA UMD logs", "Safe to delete"),
    "nvidia\\installerstaging": ("cache", "NVIDIA driver installer staging", "Safe to delete after driver install"),
    "nvidia\\netservice": ("cache", "NVIDIA NetService logs", "Safe to delete"),
    "nvidia\\dxcache": ("cache", "NVIDIA DirectX shader cache", "Safe to clear, will rebuild"),
    "nvidia\\glcache": ("cache", "NVIDIA OpenGL shader cache", "Safe to clear, will rebuild"),
    "intel package cache": ("cache", "Intel installer leftovers", "Safe to delete after install"),
    "package cache": ("cache", "Windows installer cache (MSI)", "Safe to delete - Windows recreates via WIX tools"),
    "chocolateyhttpcache": ("cache", "Chocolatey package cache", "Safe to delete"),
    "ea desktop\\logs": ("cache", "EA Desktop logs", "Safe to delete"),
    "overwolf\\temp": ("cache", "Overwolf temp files", "Safe to delete"),
    "epicinstallertemp": ("cache", "Epic installer temp", "Safe to delete"),
    "visualstudio\\packages": ("cache", "VS package cache", "VS Installer → More → Clean up"),
    # System temp locations
    "windows\\temp": ("cache", "Windows system temp", "Safe to empty periodically"),
    "software distribution\\download": ("cache", "Windows Update download cache", "Safe to delete after updates install"),
    "appdata\\local\\temp": ("cache", "User temp folder", "Safe to empty periodically"),
    "appdata\\local\\crashdumps": ("cache", "Application crash dumps", "Safe to delete old dumps"),
    # Move candidates
    "corsair": ("move", "Corsair iCUE software", "Uninstall, reinstall to D:"),
    "postgresql": ("move", "PostgreSQL database", "Move data dir or reinstall to D:"),
    "adobe creative cloud": ("move", "Adobe Creative Cloud", "CC App → Settings → Install Location"),
    "microsoft visual studio": ("move", "Visual Studio", "VS Installer → Move install location"),
    # Decision required
    "metaquotes": ("decide", "MetaTrader 5 data", "Keep if trading, else uninstall MT5"),
    "overwolf": ("decide", "Overwolf overlay/apps", "Uninstall if unused"),
    "iobit": ("decide", "IObit software (Advanced SystemCare, Driver Booster)", "Uninstall via Control Panel if unused"),
    "razer": ("decide", "Razer Synapse", "Uninstall if unused"),
    "steelseries": ("decide", "SteelSeries Engine", "Uninstall if unused"),
    "logitech": ("decide", "Logitech G Hub", "Uninstall if unused"),
}

# Folder names AND absolute paths that can never be deleted
PROTECTED_NAMES = {s.lower() for s in _c("PROTECTED_NAMES", {
    "windows","program files","program files (x86)","programdata",
    "users","system volume information","documents and settings",
    "recovery","onedrivetemp","perflogs","boot","efi",
})}
PROTECTED_PATHS = {
    p.lower() for p in _c("PROTECTED_PATHS", [
        r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)",
        r"C:\ProgramData", r"C:\Users", r"C:\Recovery",
        r"C:\System Volume Information", r"C:\Documents and Settings",
        r"C:\OneDriveTemp", r"C:\PerfLogs", r"C:\Boot", r"C:\EFI",
        r"D:\AI",
    ])
}

def is_protected(path, name):
    if name.lower() in PROTECTED_NAMES: return True
    if path.lower() in PROTECTED_PATHS: return True
    # Protect anything that IS a drive root (e.g. C:\)
    if os.path.splitdrive(path)[1] in ("\\", "/"): return True
    return False

PAGE = _c("PAGE", 25)
TW   = _c("TERMINAL_WIDTH", 80)
_MAX_FILES_SHOWN = _c("MAX_FILES_SHOWN", 8)

# ── Helpers ────────────────────────────────────────────────────────────────────
def _prog(path):
    msg = f"  Scanning: {path}"
    if len(msg) > TW-2: msg = msg[:TW-5]+"..."
    with _out_lock:
        sys.stderr.write(f"\r{DIM}{msg:<{TW}}{RS}"); sys.stderr.flush()

def _clr():
    sys.stderr.write(f"\r{' '*TW}\r"); sys.stderr.flush()

def format_size(b):
    for u in ("B","KB","MB","GB","TB"):
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"

def size_col(b):
    if b==0:        return DIM
    if b>=1<<30:    return R
    if b>=100<<20:  return Y
    if b>=10<<20:   return G
    if b>=1<<20:    return C
    return W

def is_junk(name): return name.lower() in JUNK_NAMES

def bar(used, total, w=20):
    if not total: return "-"*w
    f=int(w*used/total); p=used/total*100
    col=R if p>90 else Y if p>70 else G
    return f"{col}{'#'*f}{DIM}{'-'*(w-f)}{RS}"

# ── Drive helpers ──────────────────────────────────────────────────────────────
def get_drives():
    drives=[]; bm=_k32.GetLogicalDrives()
    for L in string.ascii_uppercase:
        if bm&1:
            d=f"{L}:\\"
            if os.path.isdir(d): drives.append(d)
        bm>>=1
    return drives

def drive_info(drive):
    lb=ctypes.create_unicode_buffer(256)
    _k32.GetVolumeInformationW(drive,lb,256,None,None,None,None,0)
    lbl=lb.value or "Local Disk"
    fr=ctypes.c_ulonglong(0); tot=ctypes.c_ulonglong(0)
    _k32.GetDiskFreeSpaceExW(drive,None,ctypes.byref(tot),ctypes.byref(fr))
    return lbl, tot.value-fr.value, tot.value, fr.value

# ── Core scan ─────────────────────────────────────────────────────────────────
def folder_size(path, progress=False):
    total = 0; cnt = 0
    
    # Update: Handle Windows MAX_PATH (260 char) limit
    abs_path = os.path.abspath(path)
    if not abs_path.startswith("\\\\?\\"):
        abs_path = "\\\\?\\" + abs_path
    
    stack = [abs_path] # Use the prefixed path
    while stack:
        cur = stack.pop()
        if progress: _prog(cur)
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat().st_size
                            cnt += 1
                    except (PermissionError, OSError): pass
        except (PermissionError, OSError): pass
    return total, cnt

def scan(root, sort_by, rev):
    dirs=[]; files=[]
    
    # Update 1: Handle Windows MAX_PATH (260 char) limit
    abs_root = os.path.abspath(root)
    if not abs_root.startswith("\\\\?\\"):
        abs_root = "\\\\?\\" + abs_root
        
    try:
        # Use the prefixed abs_root for the scan
        for e in os.scandir(abs_root):
            if e.is_dir(follow_symlinks=False): dirs.append(e)
            elif e.is_file(follow_symlinks=False):
                try: files.append((e.stat().st_size, e.name))
                except (PermissionError,OSError): pass
    except (PermissionError,OSError): return [],[]

    fi=[]
    
    # Update 2: Increase workers to leverage your Core Ultra 5's 10 cores [cite: 2026-03-05]
    # Since metadata scanning is I/O bound, 40-64 workers is often a "sweet spot."
    workers = min(max(1, len(dirs)), _c("MAX_WORKERS", 40))

    def scan_one(e):
        # e.path will automatically include the \\?\ prefix if abs_root had it
        sz, cnt = folder_size(e.path, progress=True)
        junk = is_junk(e.name)
        col = MG if junk else size_col(sz)
        tag = f"  {MG}[junk]{RS}" if junk else ""
        with _out_lock:
            _clr()
            print(f"  {col}{format_size(sz):>10}{RS}  {DIM}{cnt:>7,} files{RS}  {col}{e.name}{RS}{tag}")
            sys.stdout.flush()
        return (sz, cnt, e.path, e.name, junk)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(scan_one, e) for e in dirs]
        for future in as_completed(futures):
            try: fi.append(future.result())
            except Exception: pass

    def skey_f(x): return x[0] if sort_by=="size" else x[3].lower()
    def skey_l(x): return x[0] if sort_by=="size" else x[1].lower()
    fi.sort(key=skey_f, reverse=(sort_by=="size" and not rev) or (sort_by!="size" and rev))
    files.sort(key=skey_l, reverse=(sort_by=="size" and not rev) or (sort_by!="size" and rev))
    return fi, files

# ── Screen renderer ───────────────────────────────────────────────────────────
def _visible(state):
    v=state["items"]
    if state["q"]: v=[i for i in v if state["q"].lower() in i[3].lower()]
    if state["f"]: v=[i for i in v if i[0]>=(1<<20)]
    return v

def _resort(state, rev):
    s=state["sort"]
    kf = (lambda x:x[0]) if s=="size" else (lambda x:x[3].lower())
    kl = (lambda x:x[0]) if s=="size" else (lambda x:x[1].lower())
    asc = not (s=="size" and not rev) and not (s!="size" and rev)
    state["items"].sort(key=kf, reverse=not asc)
    state["files"].sort(key=kl, reverse=not asc)

def render(state):
    vis=_visible(state)
    tp=max(1,(len(vis)+PAGE-1)//PAGE)
    pg=max(0,min(state["pg"],tp-1)); state["pg"]=pg
    sl=pg*PAGE; chunk=vis[sl:sl+PAGE]

    os.system("cls")
    print(f"{BD}{'='*TW}{RS}")
    print(f"  {BD}Folder Size Explorer{RS}")
    crumb=state["label"]; crumb=crumb if len(crumb)<=TW-4 else "..."+crumb[-(TW-7):]
    print(f"  {DIM}{crumb}{RS}")
    print(f"{BD}{'='*TW}{RS}")

    if state["di"]:
        for drv,(lbl,used,tot,fr) in state["di"].items():
            if tot:
                print(f"  {BD}{drv}{RS} {DIM}[{lbl}]{RS} [{bar(used,tot)}] "
                      f"{size_col(used)}{format_size(used)}{RS}/{format_size(tot)} "
                      f"{DIM}{format_size(fr)} free{RS}")
        print()

    elapsed = state.get("scan_elapsed"); sfiles = state.get("scan_files"); ssize = state.get("scan_size")
    perf_str = (f"  {DIM}scanned in {elapsed:.2f}s  "
                f"({sfiles/elapsed:,.0f} files/s){RS}" if elapsed and sfiles else "")
    print(f"  sort:{BD}{state['sort']}{RS}   "
          f"filter:{BD}{'>=1MB' if state['f'] else 'off'}{RS}   "
          f"search:{BD}{repr(state['q']) if state['q'] else 'off'}{RS}   "
          f"{DIM}{len(vis)}{' of '+str(len(state['items'])) if len(vis)!=len(state['items']) else ''} folder(s){RS}")
    if perf_str and not state["history"]: print(perf_str)

    junk=[i for i in vis if i[4]]
    if junk:
        print(f"  {MG}! {len(junk)} junk folder(s) detected  —  {format_size(sum(i[0] for i in junk))} potentially recoverable{RS}")

    print(f"{DIM}{'-'*TW}{RS}")

    if not vis:
        print(f"\n  {DIM}(no folders match — clear filter/search){RS}\n")
    else:
        print(f"  {DIM}{'#':>5}   {'Size':>10}   {'Files':>8}   Name{RS}")
        print(f"  {DIM}{'-'*(TW-4)}{RS}")
        for num,(sz,cnt,path,name,junk) in enumerate(chunk, sl+1):
            col=MG if junk else size_col(sz)
            tag=f" {MG}[!]{RS}" if junk else ""
            print(f"  {DIM}{num:>5}.{RS}  {col}{format_size(sz):>10}{RS}  {DIM}{cnt:>8,}{RS}  {col}{name}{RS}{tag}")
        print()

    if state["files"] and not state["q"]:
        ftot=sum(f[0] for f in state["files"])
        print(f"  {DIM}── {len(state['files'])} file(s)  {format_size(ftot)} total ──{RS}")
        for sz,name in state["files"][:_MAX_FILES_SHOWN]:
            print(f"  {' ':>5}   {size_col(sz)}{format_size(sz):>10}{RS}  {' ':>8}  {DIM}{name}{RS}")
        if len(state["files"])>_MAX_FILES_SHOWN:
            print(f"  {DIM}... and {len(state['files'])-_MAX_FILES_SHOWN} more{RS}")
        print()

    print(f"{DIM}{'-'*TW}{RS}")
    cs=[]
    if state["history"]: cs.append(f"{BD}[b]{RS}ack")
    cs.append(f"{BD}[#]{RS} open  {DIM}delete_#{RS}")
    if tp>1: cs.append(f"{BD}[n]{RS}/{BD}[p]{RS} page {DIM}({pg+1}/{tp}){RS}")
    cs.append(f"{BD}[s]{RS}ort={DIM}{state['sort']}{RS}")
    cs.append(f"{BD}[f]{RS}ilter")
    cs.append(f"{BD}[/text]{RS} search")
    if state["q"]: cs.append(f"{BD}[.]{RS} clear")
    junk=[i for i in state["items"] if i[4]]
    if junk: cs.append(f"{MG}[j]{RS} clear {len(junk)} junk ({format_size(sum(i[0] for i in junk))}){RS}")
    cs.append(f"{BD}[q]{RS}uit")
    print("  "+"  ".join(cs))
    print(f"{DIM}{'='*TW}{RS}")

# ── Steam uninstall helper ────────────────────────────────────────────────────
def find_steam_appid(path):
    """If path is inside steamapps/common/<game>, return (appid, acf_path) else None."""
    parent = os.path.dirname(path)
    if os.path.basename(parent).lower() != "common":
        return None
    steamapps = os.path.dirname(parent)
    if os.path.basename(steamapps).lower() != "steamapps":
        return None
    game_dir = os.path.basename(path)
    try:
        for entry in os.scandir(steamapps):
            if entry.name.lower().startswith("appmanifest_") and entry.name.endswith(".acf"):
                try:
                    with open(entry.path, encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    # extract installdir value
                    for line in content.splitlines():
                        line = line.strip()
                        if line.lower().startswith('"installdir"'):
                            parts = line.split('"')
                            if len(parts) >= 4 and parts[3].lower() == game_dir.lower():
                                appid = entry.name[12:-4]  # appmanifest_<id>.acf
                                if appid.isdigit():
                                    return appid, entry.path
                except OSError:
                    pass
    except OSError:
        pass
    return None

# ── Scan stats printer ───────────────────────────────────────────────────────
def _pl(n, word):
    return f"{n:,} {word}{'s' if n != 1 else ''}"

def print_scan_stats(folders, loose_files, elapsed):
    total_files  = sum(i[1] for i in folders) + len(loose_files)
    total_size   = sum(i[0] for i in folders) + sum(f[0] for f in loose_files)
    fps          = total_files / elapsed if elapsed > 0 else 0
    avg_size     = total_size / total_files if total_files > 0 else 0
    junk         = [i for i in folders if i[4]]
    largest      = max(folders, key=lambda x: x[0], default=None)
    sep          = f"  {DIM}{'─'*(TW-2)}{RS}"

    print(f"\n{sep}")
    print(f"  {'Folders':<22} {BD}{_pl(len(folders), 'folder'):>16}{RS}")
    print(f"  {'Files':<22} {BD}{_pl(total_files, 'file'):>16}{RS}")
    print(f"  {'Total size':<22} {BD}{format_size(total_size):>16}{RS}")
    print(f"  {'Avg file size':<22} {BD}{format_size(avg_size):>16}{RS}")
    if largest:
        avail = TW - 38
        name_trunc = largest[3] if len(largest[3]) <= avail else largest[3][:avail-3]+"..."
        print(f"  {'Largest folder':<22} {size_col(largest[0])}{format_size(largest[0]):>16}{RS}  {DIM}{name_trunc}{RS}")
    if junk:
        junk_size  = sum(i[0] for i in junk)
        junk_files = sum(i[1] for i in junk)
        detail = f"{_pl(len(junk), 'folder')}, {_pl(junk_files, 'file')}"
        print(f"  {'Junk detected':<22} {MG}{format_size(junk_size):>16}  {DIM}{detail}{MG}  recoverable{RS}")
    print(sep)
    print(f"  {'Scan time':<22} {BD}{elapsed:>15.2f}s{RS}")
    print(f"  {'Throughput':<22} {BD}{fps:>13,.0f} files/s{RS}")
    print(sep)
    return total_files, total_size


def analyze_c_drive_bloat(folders):
    """Analyze C: drive folders for known bloat patterns and return categorized results."""
    cache_items = []
    move_items = []
    decide_items = []
    
    for sz, cnt, path, name, _ in folders:
        lname = name.lower()
        lpath = path.lower()
        
        for pattern, (category, desc, action) in BLOAT_PATTERNS.items():
            if pattern in lname or pattern in lpath:
                item = (sz, cnt, path, name, desc, action, category)
                if category == "cache":
                    cache_items.append(item)
                elif category == "move":
                    move_items.append(item)
                elif category == "decide":
                    decide_items.append(item)
                break
    
    return cache_items, move_items, decide_items


def print_c_drive_analysis(cache_items, move_items, decide_items):
    """Print a formatted C: drive bloat analysis report."""
    print(f"\n{BD}{' '*(TW//2-10)}{'C: DRIVE BLOAT ANALYSIS'}{RS}")
    print(f"{DIM}{'─'*TW}{RS}\n")
    
    # helper to print category block
    def print_block(items, color, title):
        if not items: return
        total = sum(i[0] for i in items)
        print(f"  {color}{BD}{title}{RS} {DIM}({format_size(total)}){RS}")
        print(f"  {DIM}{'─'*60}{RS}")
        for sz, cnt, path, name, desc, action, _ in sorted(items, key=lambda x: x[0], reverse=True):
            print(f"    {size_col(sz)}{format_size(sz):>10}{RS}  {BD}{name.upper():<20}{RS} {DIM}{desc}{RS}")
            print(f"    {'':<12} {G if color==R else C if color==Y else Y}→ {action}{RS}")
        print()

    print_block(cache_items, R, "🔴 CACHE / JUNK (Safe to delete)")
    print_block(move_items, Y, "🟡 MOVABLE (Safe to move to D:)")
    print_block(decide_items, C, "🟢 REQUIRES DECISION")


def analyze_c_drive(category=None):
    """Fast C: drive bloat analysis using targeted size lookups instead of full recursive scans."""
    print(f"\n  {BD}Scanning targeted C: drive bloat paths...{RS}\n")
    
    # Direct targeted paths to check quickly
    specific_paths = [
        # Temp locations
        (r"C:\Windows\Temp", "Windows system temp", "cache", "Safe to empty periodically"),
        (os.path.expandvars(r"%LOCALAPPDATA%\Temp"), "User temp folder", "cache", "Safe to empty periodically"),
        (r"C:\Windows\SoftwareDistribution\Download", "Windows Update download cache", "cache", "Safe to delete after updates install"),
        (os.path.expandvars(r"%LOCALAPPDATA%\CrashDumps"), "Application crash dumps", "cache", "Safe to delete old dumps"),
        
        # Shader caches
        (os.path.expandvars(r"%LOCALAPPDATA%\NVIDIA\DXCache"), "NVIDIA DirectX shader cache", "cache", "Safe to clear, will rebuild"),
        (os.path.expandvars(r"%LOCALAPPDATA%\NVIDIA\GLCache"), "NVIDIA OpenGL shader cache", "cache", "Safe to clear, will rebuild"),
        (r"C:\ProgramData\NVIDIA Corporation\NetService", "NVIDIA NetService logs", "cache", "Safe to delete"),
        (r"C:\ProgramData\NVIDIA Corporation\InstallerStaging", "NVIDIA driver installer staging", "cache", "Safe to delete after driver install"),
        (r"C:\ProgramData\NVIDIA Corporation\ShadowPlay", "NVIDIA ShadowPlay recordings/cache", "cache", "Disable ShadowPlay or clear folder"),
        
        # Package caches
        (r"C:\ProgramData\Package Cache", "Windows installer cache (MSI)", "cache", "Safe to delete - Windows recreates via WIX tools"),
        (r"C:\ProgramData\Intel Package Cache", "Intel installer leftovers", "cache", "Safe to delete after install"),
        (r"C:\ProgramData\ChocolateyHttpCache", "Chocolatey package cache", "cache", "Safe to delete"),
        
        # Software to move or check
        (r"C:\ProgramData\IObit", "IObit software (ASC, Driver Booster)", "decide", "Uninstall via Control Panel if unused"),
        (r"C:\ProgramData\Corsair", "Corsair iCUE software", "move", "Uninstall, reinstall to D:"),
        (r"C:\Program Files\PostgreSQL", "PostgreSQL database", "move", "Move data dir or reinstall to D:"),
        (r"C:\Program Files\Microsoft Visual Studio", "Visual Studio", "move", "VS Installer → Move install location"),
        (r"C:\ProgramData\MetaQuotes", "MetaTrader 5 data", "decide", "Keep if trading, else uninstall MT5"),
        (r"C:\ProgramData\Overwolf", "Overwolf overlay/apps", "decide", "Uninstall if unused"),
    ]
    
    cache_items = []
    move_items = []
    decide_items = []
    
    def get_dir_size(path):
        """Fast directory size using os.scandir (no file counting overhead)."""
        total = 0
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                        elif entry.is_dir(follow_symlinks=False):
                            total += get_dir_size(entry.path)
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    for path, desc, cat, action in specific_paths:
        if os.path.exists(path):
            sz = get_dir_size(path)
            if sz > 0:
                name = os.path.basename(path)
                item = (sz, 0, path, name, desc, action, True)
                if cat == "cache":
                    cache_items.append(item)
                elif cat == "move":
                    move_items.append(item)
                elif cat == "decide":
                    decide_items.append(item)
    
    # Filter by category if specified
    if category:
        if category in ("delete", "cache"):
            move_items = []
            decide_items = []
        elif category == "move":
            cache_items = []
            decide_items = []
        elif category == "decide":
            cache_items = []
            move_items = []
    
    print_c_drive_analysis(cache_items, move_items, decide_items)
    
    # Summary
    total_bloat = sum(i[0] for i in cache_items + move_items + decide_items)
    print(f"\n  {BD}Total identified bloat: {format_size(total_bloat)}{RS}")
    print(f"  {G}Immediately recoverable (cache): {format_size(sum(i[0] for i in cache_items))}{RS}")
    print(f"  {C}Movable to D:: {format_size(sum(i[0] for i in move_items))}{RS}")
    print(f"  {Y}Requires decision: {format_size(sum(i[0] for i in decide_items))}{RS}")

    return cache_items, move_items, decide_items


def clean_cache_items(dry_run=True):
    """Delete cache folders identified by analyze_c_drive.
    Returns (deleted_bytes, deleted_count, errors_list).
    """
    cache_items, _, _ = analyze_c_drive(category="cache")
    
    if not cache_items:
        print("No cache items to clean.")
        return 0, 0, []
    
    deleted_bytes = 0
    deleted_count = 0
    errors = []
    
    print(f"\n{'DRY RUN - ' if dry_run else ''}Deleting {len(cache_items)} cache folders...\n")
    
    for sz, cnt, path, name, desc, action, _ in cache_items:
        print(f"  {'Would delete' if dry_run else 'Deleting'}: {path} ({format_size(sz)})")
        
        if not dry_run:
            try:
                shutil.rmtree(path)
                deleted_bytes += sz
                deleted_count += 1
                print(f"    {G}✓ Deleted{RS}")
            except Exception as exc:
                err = f"Failed to delete {path}: {exc}"
                errors.append(err)
                print(f"    {R}✗ {err}{RS}")
    
    if not dry_run:
        print(f"\n  {BD}Total freed: {format_size(deleted_bytes)} ({deleted_count} folders){RS}")
        if errors:
            print(f"  {R}Errors: {len(errors)}{RS}")
            for e in errors:
                print(f"    {e}")
    
    return deleted_bytes, deleted_count, errors


def pick_drives(drives):
    os.system("cls")
    print(f"{BD}{'='*TW}{RS}")
    print(f"  {BD}Folder Size Explorer  —  Select Drive(s) to Scan{RS}")
    print(f"{BD}{'='*TW}{RS}\n")
    infos=[]
    for d in drives:
        try: infos.append((d,)+drive_info(d))
        except Exception: infos.append((d,"Unknown",0,0,0))
    for i,(d,lbl,used,tot,fr) in enumerate(infos,1):
        if tot:
            p=used/tot*100; col=R if p>90 else Y if p>70 else G
            s=f"[{bar(used,tot)}] {col}{format_size(used)}{RS}/{format_size(tot)} {DIM}{format_size(fr)} free{RS}"
        else: s=f"{DIM}(unknown){RS}"
        print(f"  {BD}{i}.{RS}  {BD}{d}{RS}  {DIM}[{lbl}]{RS}  {s}")
    print(f"\n{DIM}{'-'*TW}{RS}")
    print(f"  Enter numbers {DIM}(e.g. 2 3){RS}  or press {BD}Enter{RS} to scan all")
    print(f"{DIM}{'='*TW}{RS}")
    try: raw=input(f"\n  {BD}>{RS} ").strip()
    except (EOFError,KeyboardInterrupt): return []
    if not raw: return drives
    seen=set(); res=[]
    for t in raw.split():
        if t.isdigit():
            idx=int(t)-1
            if 0<=idx<len(infos):
                d=infos[idx][0]
                if d not in seen: seen.add(d); res.append(d)
    return res if res else drives

# ── Browser ───────────────────────────────────────────────────────────────────
def browse(roots, sort_by, rev):
    print(f"\n  {BD}Scanning, please wait...{RS}\n"); sys.stdout.flush()

    di={}
    for root in roots:
        drv=os.path.splitdrive(root)[0]+"\\"
        if drv.upper()==root.upper():
            try: di[root]=drive_info(root)
            except Exception: pass

    t0 = time.perf_counter()
    ai=[]; af=[]
    total_files = 0
    for root in roots:
        if len(roots)>1: print(f"\n  {DIM}[ {root} ]{RS}"); sys.stdout.flush()
        fi,fl=scan(root,sort_by,rev); ai.extend(fi); af.extend(fl)
        total_files += sum(i[1] for i in fi) + len(fl)
    elapsed = time.perf_counter() - t0

    def sk_f(x): return x[0] if sort_by=="size" else x[3].lower()
    def sk_l(x): return x[0] if sort_by=="size" else x[1].lower()
    asc=not((sort_by=="size" and not rev) or (sort_by!="size" and rev))
    ai.sort(key=sk_f, reverse=not asc); af.sort(key=sk_l, reverse=not asc)

    fps = total_files / elapsed if elapsed > 0 else 0
    total_files, total_size = print_scan_stats(ai, af, elapsed)
    print(f"  Press Enter to open the browser...")
    try: input()
    except (EOFError,KeyboardInterrupt): return

    state={
        "label": ", ".join(roots) if len(roots)>1 else roots[0],
        "items": ai, "files": af,
        "di":    di if di else None,
        "pg":0, "sort":sort_by, "f":False, "q":"",
        "history":[], "first_render":True,
        "scan_elapsed": elapsed, "scan_files": total_files, "scan_size": total_size,
        "first_render": True,
    }

    while True:
        render(state)
        try: choice=input(f"\n  {BD}>{RS} ").strip()
        except (EOFError,KeyboardInterrupt): break
        low=choice.lower()

        if   low=="q": break
        elif low=="b":
            if state["history"]:
                prev=state["history"].pop(); state.update(prev)
        elif low=="n":
            vis=_visible(state); tp=max(1,(len(vis)+PAGE-1)//PAGE)
            if state["pg"]<tp-1: state["pg"]+=1
        elif low=="p":
            if state["pg"]>0: state["pg"]-=1
        elif low=="s":
            state["sort"]="name" if state["sort"]=="size" else "size"
            _resort(state,rev); state["pg"]=0
        elif low=="f":
            state["f"]=not state["f"]; state["pg"]=0
        elif low==".":
            state["q"]=""; state["pg"]=0
        elif low=="j":
            junk=[i for i in state["items"] if i[4]]
            # silently exclude protected entries (shouldn't be junk, but be safe)
            junk=[i for i in junk if not is_protected(i[2], i[3])]
            if not junk:
                input(f"\n  {DIM}No junk folders found.{RS}  Press Enter...")
            else:
                os.system("cls")
                print(f"\n  {MG}{BD}Junk folders to delete:{RS}\n")
                for sz,cnt,path,name,_ in junk:
                    print(f"  {MG}{format_size(sz):>10}{RS}  {path}")
                total_junk=sum(i[0] for i in junk)
                print(f"\n  {Y}Total: {format_size(total_junk)}{RS}")
                print(f"  {Y}This cannot be undone.{RS}")
                try: confirm=input(f"\n  Type {BD}YES{RS} to delete all: ").strip()
                except (EOFError,KeyboardInterrupt): confirm=""
                if confirm=="YES":
                    freed=0
                    for sz,cnt,path,name,_ in junk:
                        try:
                            if name.lower() == "$recycle.bin":
                                import subprocess
                                subprocess.run(
                                    ["powershell","-NoProfile","-Command",
                                     "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"],
                                    check=False, capture_output=True
                                )
                                print(f"  {G}Emptied:{RS}  {path}  {DIM}(via Clear-RecycleBin){RS}")
                            else:
                                shutil.rmtree(path, ignore_errors=True)
                                print(f"  {G}Deleted:{RS}  {path}")
                            freed+=sz
                        except Exception as ex:
                            print(f"  {R}Failed:{RS}   {path}  ({ex})")
                    state["items"]=[i for i in state["items"] if not i[4]]
                    print(f"\n  {G}Done. Freed ~{format_size(freed)}{RS}")
                    input("  Press Enter to continue...")
                else:
                    input(f"\n  {DIM}Cancelled.{RS}  Press Enter...")
        elif low.startswith("/"):
            state["q"]=choice[1:].strip(); state["pg"]=0
        elif low.startswith("delete_"):
            token=low[7:]
            if token.isdigit():
                vis=_visible(state); idx=int(token)-1
                if 0<=idx<len(vis):
                    sz,cnt,path,name,_=vis[idx]
                    if is_protected(path, name):
                        input(f"\n  {R}Protected: '{name}' cannot be deleted.{RS}  Press Enter...")
                    else:
                        steam = find_steam_appid(path)
                        os.system("cls")
                        if steam:
                            appid, acf = steam
                            print(f"\n  {C}{BD}Steam game detected:{RS}  {name}  {DIM}(AppID {appid}){RS}")
                            print(f"  {DIM}{path}{RS}")
                            print(f"  {Y}Size: {format_size(sz)}   Files: {cnt:,}{RS}\n")
                            print(f"  {BD}[1]{RS} Uninstall via Steam  {DIM}(recommended — clean uninstall){RS}")
                            print(f"  {BD}[2]{RS} Delete folder only   {DIM}(skips Steam, may leave registry/shortcuts){RS}")
                            print(f"  {BD}[q]{RS} Cancel")
                            try: action=input(f"\n  {BD}>{RS} ").strip().lower()
                            except (EOFError,KeyboardInterrupt): action="q"
                            if action == "1":
                                import subprocess
                                subprocess.Popen(["cmd","/c",f"start steam://uninstall/{appid}"])
                                print(f"\n  {G}Steam uninstall launched for AppID {appid}.{RS}")
                                print(f"  {DIM}Steam will handle removal — you can close this and confirm in Steam.{RS}")
                                state["items"]=[i for i in state["items"] if i[2]!=path]
                            elif action == "2":
                                try:
                                    shutil.rmtree(path)
                                    state["items"]=[i for i in state["items"] if i[2]!=path]
                                    print(f"\n  {G}Deleted. Freed ~{format_size(sz)}{RS}")
                                except Exception as ex:
                                    print(f"\n  {R}Failed: {ex}{RS}")
                            else:
                                print(f"\n  {DIM}Cancelled.{RS}")
                        else:
                            print(f"\n  {R}{BD}Delete folder:{RS}  {path}")
                            print(f"  {Y}Size: {format_size(sz)}   Files: {cnt:,}{RS}")
                            print(f"  {Y}This cannot be undone.{RS}")
                            try: confirm=input(f"\n  Type {BD}YES{RS} to delete: ").strip()
                            except (EOFError,KeyboardInterrupt): confirm=""
                            if confirm=="YES":
                                try:
                                    shutil.rmtree(path)
                                    state["items"]=[i for i in state["items"] if i[2]!=path]
                                    print(f"\n  {G}Deleted. Freed ~{format_size(sz)}{RS}")
                                except Exception as ex:
                                    print(f"\n  {R}Failed: {ex}{RS}")
                            else:
                                print(f"\n  {DIM}Cancelled.{RS}")
                        input("  Press Enter to continue...")
                else:
                    input(f"\n  {Y}No item #{token}.{RS}  Press Enter...")
            else:
                input(f"\n  {Y}Usage: delete_<number>{RS}  Press Enter...")
        elif choice.isdigit():
            vis=_visible(state); idx=int(choice)-1
            if 0<=idx<len(vis):
                _,_,path,name,_=vis[idx]
                os.system("cls")
                print(f"\n  {BD}Scanning:{RS}  {path}\n"); sys.stdout.flush()
                t0=time.perf_counter()
                ch,cf=scan(path,state["sort"],rev)
                sub_elapsed=time.perf_counter()-t0
                sub_files=sum(i[1] for i in ch)+len(cf)
                fps=sub_files/sub_elapsed if sub_elapsed>0 else 0
                sub_files, sub_size = print_scan_stats(ch, cf, sub_elapsed)
                print(f"  Press Enter to open the browser...")
                try: input()
                except (EOFError,KeyboardInterrupt): break
                saved={k:v for k,v in state.items() if k!="history"}
                state["history"].append(saved)
                state["label"]=path; state["items"]=ch; state["files"]=cf
                state["di"]=None; state["pg"]=0; state["q"]=""
                state["scan_elapsed"]=sub_elapsed; state["scan_files"]=sub_files; state["scan_size"]=sub_size
                state["first_render"]=True
            else:
                input(f"\n  {Y}No item #{choice}.{RS} Press Enter...")
        else:
            input(f"\n  {DIM}Commands: # b n p s f /query . q{RS}  Press Enter...")

    os.system("cls"); print(f"  {G}Goodbye!{RS}\n")

# ── GUI mode ─────────────────────────────────────────────────────────────────
def scan_for_ui(root, sort_by="size", rev=False):
    dirs=[]; files=[]
    abs_root = os.path.abspath(root)
    if not abs_root.startswith("\\\\?\\"):
        abs_root = "\\\\?\\" + abs_root
    try:
        for e in os.scandir(abs_root):
            if e.is_dir(follow_symlinks=False):
                dirs.append(e)
            elif e.is_file(follow_symlinks=False):
                try:
                    files.append((e.stat().st_size, e.name))
                except (PermissionError, OSError):
                    pass
    except (PermissionError, OSError):
        return [], []

    items=[]
    for e in dirs:
        try:
            sz, cnt = folder_size(e.path, progress=False)
            items.append((sz, cnt, e.path, e.name, is_junk(e.name)))
        except Exception:
            continue

    def skey_f(x): return x[0] if sort_by=="size" else x[3].lower()
    def skey_l(x): return x[0] if sort_by=="size" else x[1].lower()
    asc = not ((sort_by=="size" and not rev) or (sort_by!="size" and rev))
    items.sort(key=skey_f, reverse=not asc)
    files.sort(key=skey_l, reverse=not asc)
    return items, files


def launch_gui():
    if tk is None or ttk is None:
        print("Tkinter is not available in this Python install.", file=sys.stderr)
        sys.exit(1)

    app = tk.Tk()
    app.title("Folder Size Explorer")
    app.geometry("1100x700")
    app.minsize(900, 550)

    root_var = tk.StringVar(value=os.getcwd())
    sort_var = tk.StringVar(value="size")
    filter_var = tk.BooleanVar(value=False)
    status_var = tk.StringVar(value="Ready")

    top = ttk.Frame(app, padding=12)
    top.pack(fill="x")

    ttk.Label(top, text="Folder:").pack(side="left")
    path_entry = ttk.Entry(top, textvariable=root_var, width=80)
    path_entry.pack(side="left", fill="x", expand=True, padx=(8, 8))

    ttk.Button(top, text="Browse", command=lambda: _browse_for_folder(root_var)).pack(side="left")
    ttk.Button(top, text="Scan", command=lambda: _scan_in_background()).pack(side="left", padx=(8, 0))

    filters = ttk.Frame(app)
    filters.pack(fill="x", padx=12, pady=(0, 8))
    ttk.Checkbutton(filters, text="Only folders >= 1 MB", variable=filter_var, command=lambda: _scan_in_background()).pack(side="left")
    ttk.Label(filters, text="Sort:").pack(side="left", padx=(18, 6))
    ttk.Combobox(filters, textvariable=sort_var, values=["size", "name"], state="readonly", width=10).pack(side="left")
    sort_var.trace_add("write", lambda *_: _scan_in_background())

    table = ttk.Treeview(app, columns=("name", "size", "files", "path", "junk"), show="headings")
    table.heading("name", text="Name")
    table.heading("size", text="Size")
    table.heading("files", text="Files")
    table.heading("path", text="Path")
    table.heading("junk", text="Junk")
    table.column("name", width=260, anchor="w")
    table.column("size", width=120, anchor="e")
    table.column("files", width=100, anchor="e")
    table.column("path", width=420, anchor="w")
    table.column("junk", width=80, anchor="center")
    table.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    ttk.Label(app, textvariable=status_var, relief="sunken", anchor="w", padding=(8, 4)).pack(fill="x", padx=12, pady=(0, 12))

    def _set_rows(rows):
        for child in table.get_children():
            table.delete(child)
        for sz, cnt, path, name, junk in rows:
            table.insert("", tk.END, values=(name, format_size(sz), f"{cnt:,}", path, "Junk" if junk else ""))

    def _browse_for_folder(var):
        selected = filedialog.askdirectory(title="Choose a folder to scan")
        if selected:
            var.set(selected)
            _scan_in_background()

    def _scan_in_background():
        target = root_var.get().strip()
        if not target:
            status_var.set("Choose a folder to scan")
            return
        if not os.path.isdir(target):
            status_var.set(f"Not a folder: {target}")
            return

        status_var.set(f"Scanning {target}...")
        app.update_idletasks()

        def worker():
            try:
                items, _ = scan_for_ui(target, sort_by=sort_var.get(), rev=False)
                if filter_var.get():
                    items = [i for i in items if i[0] >= (1 << 20)]
                app.after(0, _set_rows, items)
                app.after(0, lambda: status_var.set(f"Found {len(items)} folders in {target}"))
            except Exception as exc:
                app.after(0, lambda: status_var.set(f"Scan error: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _open_selected():
        sel = table.selection()
        if not sel:
            return
        values = table.item(sel[0], "values")
        path = values[3]
        if path and os.path.isdir(path):
            os.startfile(path)

    def _delete_selected():
        sel = table.selection()
        if not sel:
            messagebox.showinfo("Delete", "Select a folder first.")
            return
        values = table.item(sel[0], "values")
        path = values[3]
        name = values[0]
        if not path or not os.path.isdir(path):
            messagebox.showwarning("Delete", "That folder no longer exists.")
            return
        if is_protected(path, name):
            messagebox.showwarning("Protected", f"'{name}' is protected and cannot be deleted.")
            return
        if not messagebox.askyesno("Delete folder", f"Delete this folder?\n\n{name}\n{path}"):
            return
        try:
            # winshell.delete_file(path)
            shutil.rmtree(path)
            table.delete(sel[0])
            status_var.set(f"Moved to Recycle Bin: {path}")
        except Exception as exc:
            messagebox.showerror("Delete failed", str(exc))
            status_var.set(f"Delete failed: {exc}")

    table.bind("<Double-1>", lambda _event: _open_selected())
    ttk.Button(app, text="Open folder", command=_open_selected).pack(anchor="e", padx=12, pady=(0, 8))
    ttk.Button(app, text="Delete selected", command=_delete_selected).pack(anchor="e", padx=12, pady=(0, 8))

    _scan_in_background()
    app.mainloop()


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    p=argparse.ArgumentParser(description="Smart interactive folder size explorer.")
    p.add_argument("path",nargs="?",default=None)
    p.add_argument("--sort",choices=["size","name"],default="size")
    p.add_argument("--reverse",action="store_true")
    p.add_argument("--drives",nargs="+",metavar="LETTER")
    p.add_argument("--analyze-c", action="store_true", help="Run automated C: drive analysis report.")
    p.add_argument("--category", choices=["delete", "move", "decide", "cache"], help="Filter C: drive analysis by category (delete/cache, move, decide).")
    p.add_argument("--clean", action="store_true", help="Actually delete cache folders (requires --category cache).")
    p.add_argument("--force", action="store_true", help="Actually perform deletion (without this, --clean does a dry-run).")
    p.add_argument("--json", action="store_true", help="Output JSON for programmatic use")
    p.add_argument("--ui", action="store_true", help="Open the graphical Tkinter UI instead of the terminal browser.")
    p.add_argument("--cli", action="store_true", help="Force the original terminal browser even if a GUI is available.")
    a=p.parse_args()

    if a.clean:
        if not a.category or a.category not in ("delete", "cache"):
            print("Error: --clean requires --category cache (or --category delete)")
            sys.exit(1)
        clean_cache_items(dry_run=not a.force)
        return

    if a.analyze_c:
        if a.json:
            cache_items, move_items, decide_items = analyze_c_drive()
            def to_json(items):
                return [{'size': i[0], 'files': i[1], 'path': i[2], 'name': i[3], 'desc': i[4], 'action': i[5]} for i in items]
            out = {
                'summary': {
                    'cache': sum(i[0] for i in cache_items),
                    'move': sum(i[0] for i in move_items),
                    'decide': sum(i[0] for i in decide_items),
                    'cacheCount': len(cache_items),
                    'moveCount': len(move_items),
                    'decideCount': len(decide_items),
                },
                'cache': to_json(cache_items),
                'move': to_json(move_items),
                'decide': to_json(decide_items),
            }
            if a.category:
                out = {k: v for k, v in out.items() if k == a.category or k == 'summary'}
            print(json.dumps(out))
        else:
            analyze_c_drive(a.category)
        return

    if a.ui or (not a.path and not a.drives and not a.cli and tk is not None and sys.stdout is not None and sys.stdin is not None and hasattr(sys.stdin, 'isatty') and sys.stdin.isatty() is False):
        launch_gui(); return

    if a.path:
        root=os.path.abspath(a.path)
        if not os.path.isdir(root):
            print(f"Error: not a directory: {root}",file=sys.stderr); sys.exit(1)
        roots=[root]
    else:
        drives=get_drives()
        if not drives: print("No drives found.",file=sys.stderr); sys.exit(1)
        if a.drives:
            wanted={l.upper().rstrip(":\\") for l in a.drives}
            drives=[d for d in drives if d[0].upper() in wanted]
            if not drives: print("None of those drives found.",file=sys.stderr); sys.exit(1)
            roots=drives
        else:
            roots=pick_drives(drives)
            if not roots: print("  No drives selected."); sys.exit(0)

    browse(roots, a.sort, a.reverse)

if __name__=="__main__":
    main()