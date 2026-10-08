#!/usr/bin/env python3
"""
Storage Monitor Daemon - Background service for automatic C: drive maintenance.
Runs as a Windows service or standalone daemon.
"""
import json
import os
import sys
import time
import signal
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# Ensure project root on path
PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

import folder_size as fs

CONFIG_PATH = PROJECT_DIR / "storage_monitor_config.json"
LOG_PATH = PROJECT_DIR / "logs" / "storage_monitor.log"
STATE_PATH = PROJECT_DIR / "logs" / "storage_monitor_state.json"

DEFAULT_CONFIG = {
    "scan_interval_minutes": 60,
    "critical_threshold_gb": 5,
    "warning_threshold_gb": 10,
    "auto_clean_categories": ["cache"],
    "auto_clean_dry_run": False,
    "require_admin_for_protected": True,
    "notify_on_clean": True,
    "notify_on_critical": True,
    "max_log_size_mb": 10,
    "protected_paths": [
        "C:\\Windows",
        "C:\\Program Files",
        "C:\\Program Files (x86)",
        "C:\\ProgramData",
        "C:\\Users"
    ],
    "safe_delete_patterns": [
        "NVIDIA\\DXCache",
        "NVIDIA\\GLCache",
        "NVIDIA\\NetService",
        "NVIDIA\\InstallerStaging",
        "NVIDIA\\ShadowPlay",
        "Intel\\Package Cache",
        "Package Cache",
        "ChocolateyHttpCache",
        "CrashDumps",
        "SoftwareDistribution\\Download",
        "Temp"
    ]
}


class StorageMonitor:
    def __init__(self, config: Dict):
        self.config = config
        self.running = False
        self.scan_thread: Optional[threading.Thread] = None
        self.setup_logging()
        self.load_state()

    def setup_logging(self):
        LOG_PATH.parent.mkdir(exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(LOG_PATH, encoding="utf-8"),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def load_state(self):
        self.state = {
            "last_scan": None,
            "last_clean": None,
            "total_freed_bytes": 0,
            "clean_history": []
        }
        if STATE_PATH.exists():
            try:
                with open(STATE_PATH) as f:
                    self.state.update(json.load(f))
            except Exception:
                pass

    def save_state(self):
        STATE_PATH.parent.mkdir(exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump(self.state, f, indent=2, default=str)

    def get_c_free_gb(self) -> float:
        usage = fs.drive_info("C:")
        return usage["free"] / (1024**3)

    def scan_and_analyze(self) -> Dict:
        """Run the same analysis as `clean` command."""
        cache_items, move_items, decide_items = fs.analyze_c_drive()
        
        def to_dict(items):
            return [{"size": i[0], "files": i[1], "path": i[2], "name": i[3], "desc": i[4], "action": i[5]} for i in items]
        
        return {
            "cache": to_dict(cache_items),
            "move": to_dict(move_items),
            "decide": to_dict(decide_items)
        }

    def format_bytes(self, b: int) -> str:
        return fs.format_size(b) if hasattr(fs, "format_size") else f"{b / 1024**3:.1f} GB"

    def safe_delete(self, path: str) -> bool:
        """Delete only if path matches safe patterns."""
        path_lower = path.lower()
        for pattern in self.config["safe_delete_patterns"]:
            if pattern.lower() in path_lower:
                try:
                    import shutil
                    shutil.rmtree(path)
                    self.logger.info(f"Auto-deleted: {path}")
                    return True
                except Exception as e:
                    self.logger.warning(f"Failed to delete {path}: {e}")
                    return False
        return False

    def auto_clean(self, analysis: Dict) -> Dict:
        """Run automatic cleanup based on config."""
        results = {"deleted": [], "failed": [], "skipped": []}
        
        for category in self.config["auto_clean_categories"]:
            items = analysis.get(category, [])
            for item in items:
                if self.config["auto_clean_dry_run"]:
                    results["skipped"].append(f"[DRY-RUN] {item['path']} ({self.format_bytes(item['size'])})")
                    continue
                
                if self.safe_delete(item["path"]):
                    results["deleted"].append(f"{item['path']} ({self.format_bytes(item['size'])})")
                    self.state["total_freed_bytes"] += item["size"]
                    self.state["clean_history"].append({
                        "time": datetime.now().isoformat(),
                        "path": item["path"],
                        "size": item["size"],
                        "category": category
                    })
                else:
                    results["failed"].append(f"{item['path']} (not in safe patterns or locked)")
        
        self.state["last_clean"] = datetime.now().isoformat()
        self.save_state()
        return results

    def check_and_act(self):
        free_gb = self.get_c_free_gb()
        self.logger.info(f"C: free space: {free_gb:.1f} GB")
        
        if free_gb < self.config["critical_threshold_gb"]:
            self.logger.warning(f"CRITICAL: C: drive below {self.config['critical_threshold_gb']} GB free!")
            if self.config["notify_on_critical"]:
                self.notify(f"CRITICAL: C: drive has only {free_gb:.1f} GB free!")
        
        elif free_gb < self.config["warning_threshold_gb"]:
            self.logger.warning(f"WARNING: C: drive below {self.config['warning_threshold_gb']} GB free")
        
        # Run analysis
        analysis = self.scan_and_analyze()
        total_cache = sum(i["size"] for i in analysis.get("cache", []))
        self.logger.info(f"Cache bloat detected: {self.format_bytes(total_cache)}")
        
        # Auto-clean if enabled
        if self.config["auto_clean_categories"]:
            results = self.auto_clean(analysis)
            if results["deleted"] and self.config["notify_on_clean"]:
                freed = sum(item["size"] for cat in analysis.values() for item in cat if cat == "cache")
                self.notify(f"Auto-clean freed {self.format_bytes(self.state['total_freed_bytes'])} total")
            
            for msg in results["deleted"]:
                self.logger.info(f"CLEANED: {msg}")
            for msg in results["failed"]:
                self.logger.warning(f"SKIPPED: {msg}")
        
        self.state["last_scan"] = datetime.now().isoformat()
        self.save_state()

    def notify(self, message: str):
        """Send Windows toast notification."""
        try:
            from win10toast import ToastNotifier
            toaster = ToastNotifier()
            toaster.show_toast("Storage Monitor", message, duration=10, threaded=True)
        except ImportError:
            self.logger.info(f"NOTIFICATION: {message}")

    def run_scan_loop(self):
        interval = self.config["scan_interval_minutes"] * 60
        self.logger.info(f"Starting scan loop (interval: {self.config['scan_interval_minutes']} min)")
        
        while self.running:
            try:
                self.check_and_act()
            except Exception as e:
                self.logger.error(f"Scan error: {e}")
            
            # Sleep in small chunks to allow quick shutdown
            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)

    def start(self):
        if self.running:
            return
        self.running = True
        self.scan_thread = threading.Thread(target=self.run_scan_loop, daemon=True)
        self.scan_thread.start()
        self.logger.info("Storage Monitor started")

    def stop(self):
        self.running = False
        if self.scan_thread:
            self.scan_thread.join(timeout=5)
        self.logger.info("Storage Monitor stopped")


def load_config() -> Dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            user_config = json.load(f)
        return {**DEFAULT_CONFIG, **user_config}
    return DEFAULT_CONFIG.copy()


def save_config(config: Dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def run_daemon():
    config = load_config()
    monitor = StorageMonitor(config)
    
    def signal_handler(sig, frame):
        monitor.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    monitor.start()
    try:
        while monitor.running:
            time.sleep(1)
    except KeyboardInterrupt:
        monitor.stop()


def install_service():
    """Install as Windows service using NSSM (Non-Sucking Service Manager)."""
    import subprocess
    nssm = PROJECT_DIR / "nssm.exe"
    if not nssm.exists():
        print("Download nssm.exe to project root from https://nssm.cc/download")
        return False
    
    python_exe = sys.executable
    script = str(PROJECT_DIR / "storage_monitor.py")
    
    try:
        subprocess.run([str(nssm), "install", "StorageMonitor", python_exe, script, "daemon"], check=True)
        subprocess.run([str(nssm), "set", "StorageMonitor", "Start", "SERVICE_AUTO_START"], check=True)
        subprocess.run([str(nssm), "set", "StorageMonitor", "Description", "Automatic C: drive storage maintenance"], check=True)
        print("Service installed. Run 'nssm start StorageMonitor' to start.")
        return True
    except Exception as e:
        print(f"Failed to install service: {e}")
        return False


def uninstall_service():
    import subprocess
    nssm = PROJECT_DIR / "nssm.exe"
    if nssm.exists():
        subprocess.run([str(nssm), "remove", "StorageMonitor", "confirm"], check=False)
        print("Service removed.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "daemon":
            run_daemon()
        elif cmd == "install-service":
            install_service()
        elif cmd == "uninstall-service":
            uninstall_service()
        elif cmd == "config":
            config = load_config()
            print(json.dumps(config, indent=2))
        elif cmd == "test":
            config = load_config()
            monitor = StorageMonitor(config)
            analysis = monitor.scan_and_analyze()
            print(json.dumps(analysis, indent=2, default=str))
        elif cmd == "clean":
            config = load_config()
            monitor = StorageMonitor(config)
            analysis = monitor.scan_and_analyze()
            results = monitor.auto_clean(analysis)
            print(json.dumps(results, indent=2))
        else:
            print("Usage: storage_monitor.py [daemon|install-service|uninstall-service|config|test|clean]")
    else:
        run_daemon()