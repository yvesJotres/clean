# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║              folder_size.py  —  User Configuration                         ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ── Performance ───────────────────────────────────────────────────────────────

# Max threads used when scanning top-level folders in parallel.
# Raise for NVMe/SSD, lower for HDD (HDD benefits from sequential reads).
# Recommended: 8–32 for NVMe, 2–4 for HDD.
MAX_WORKERS = 32

# ── Display ───────────────────────────────────────────────────────────────────

# Rows per page in the browser.
PAGE = 25

# Terminal width in characters (used for truncation and separator lines).
TERMINAL_WIDTH = 80

# Max loose files shown under "── N file(s) ──" before "... and N more".
MAX_FILES_SHOWN = 8

# ── Junk Detection ────────────────────────────────────────────────────────────

# Folder names (case-insensitive) that are flagged as junk and highlighted.
# These are candidates for deletion via [j].
JUNK_NAMES = {
    "temp", "$recycle.bin", "cache", "tmp", "crashdumps", "squirreltemp",
    "pip", "npm-cache", "pypa", "__pycache__", ".cache",
    "lm-studio-updater", "curseforge-updater", "prefetch",
}

# ── Protection ────────────────────────────────────────────────────────────────

# Folder names (case-insensitive) that can NEVER be deleted, regardless of path.
PROTECTED_NAMES = {
    "windows", "program files", "program files (x86)", "programdata",
    "users", "system volume information", "documents and settings",
    "recovery", "onedrivetemp", "perflogs", "boot", "efi",
}

# Absolute paths that can NEVER be deleted (case-insensitive on Windows).
# Drive roots are always protected automatically — no need to list them here.
PROTECTED_PATHS = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
    r"C:\Users",
    r"C:\Recovery",
    r"C:\System Volume Information",
    r"C:\Documents and Settings",
    r"C:\OneDriveTemp",
    r"C:\PerfLogs",
    r"C:\Boot",
    r"C:\EFI",
    r"D:\AI",       # AI workspace — never touch
]
