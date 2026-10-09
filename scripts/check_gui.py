"""GUI smoke test: compile all modules, launch MainWindow offscreen, run one refresh."""
import os
import sys
import traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["QT_QPA_PLATFORM"] = "offscreen"


def hook(exc_type, exc_value, tb):
    text = "".join(traceback.format_exception(exc_type, exc_value, tb))
    print("UNCAUGHT:", text, flush=True)
    Path("scripts/crash.txt").write_text(text, encoding="utf-8")


sys.excepthook = hook

import py_compile
for f in sorted(list(Path("gateway").rglob("[a-z]*.py"))):
    py_compile.compile(str(f), doraise=True)
print("compile ok", flush=True)

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from gateway.config import load_config
from gateway.keystore import KeyStore
from gateway.logger import RequestLogger
from gateway.limiter import RateLimiter
from gateway.ui.main_window import MainWindow

app = QApplication(sys.argv)
cfg = load_config()
print("upstreams:", [(u.name, u.base_url) for u in cfg.upstreams], "port", cfg.port, flush=True)
win = MainWindow(cfg, KeyStore(), RequestLogger(), RateLimiter())
win.resize(1080, 720)
win.show()


def phase1():
    print("P1 refresh begin", flush=True)
    win.refresh()
    print("P1 refresh triggered (worker thread)", flush=True)
    QTimer.singleShot(3000, phase2)


def phase2():
    print("P2 stats:", {k: win.stats._cards[k].value.text() for k in win.stats._cards}, flush=True)
    print("P2 rows in request table:", win.request_model.rowCount(), flush=True)
    print("P2 rows in usage table:", win.usage_model.rowCount(), flush=True)
    print("P3 quitting app", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        app.quit()
    finally:
        os._exit(0)


QTimer.singleShot(400, phase1)
QTimer.singleShot(12000, lambda: (print("TIMEOUT", flush=True), os._exit(3)))
sys.exit(app.exec())
