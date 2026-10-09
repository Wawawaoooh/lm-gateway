"""Windows service for the Strata 125B accelerator via ctypes (no pywin32).

Runs as:  C:\Python314\python.exe F:\Peterpertrilli\scripts\strata_svc.py
Registered as service "strata" (auto start). Stopping the service kills the
whole engine process tree, which unloads the model and frees RAM/VRAM.
Also runs in a plain console for testing (dispatcher fails, falls back to direct run).
"""
import ctypes
import os
import subprocess
import sys
import threading
import time
import traceback
from ctypes import wintypes
from pathlib import Path

STRATA_DIR = r"F:\Peterpertrilli\Strata"
STRATA_PY = os.path.join(STRATA_DIR, ".venv", "Scripts", "python.exe")
MODEL_POINTER = os.path.join(STRATA_DIR, "service-model.txt")
DEFAULT_MODEL = "iq2_xs"
LOG_PATH = r"F:\cloudflare\strata_service.log"


def _model_config() -> str:
    """Config json chosen by the pointer file (GUI writes it to switch models)."""
    try:
        name = Path(MODEL_POINTER).read_text(encoding="utf-8").strip()
    except OSError:
        name = ""
    cfg = Path(STRATA_DIR) / f"strata-{name}.json"
    if name and cfg.exists():
        return str(cfg)
    return os.path.join(STRATA_DIR, f"strata-{DEFAULT_MODEL}.json")


def _server_args() -> list:
    return [
        os.path.join(STRATA_DIR, "serve", "server.py"),
        "--engine", "strata",
        "--config", _model_config(),
        "--port", "8080",
        # no --open: a service must not pop a browser on boot
    ]

advapi32 = ctypes.windll.advapi32
kernel32 = ctypes.windll.kernel32

SERVICE_NAME = "strata"


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD),
        ("dwCurrentState", wintypes.DWORD),
        ("dwControlsAccepted", wintypes.DWORD),
        ("dwWin32ExitCode", wintypes.DWORD),
        ("dwServiceSpecificExitCode", wintypes.DWORD),
        ("dwCheckPoint", wintypes.DWORD),
        ("dwWaitHint", wintypes.DWORD),
    ]


class SERVICE_TABLE_ENTRY(ctypes.Structure):
    _fields_ = [
        ("lpServiceName", wintypes.LPWSTR),
        ("lpServiceProc", ctypes.c_void_p),
    ]


HANDLER_EX = ctypes.WINFUNCTYPE(
    None, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.LPWSTR))
SERVICE_MAIN = ctypes.WINFUNCTYPE(
    None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))

_stop_event = threading.Event()
_ssh = [None]
_child = [None]


def _log(msg: str):
    try:
        with open(LOG_PATH, "a", buffering=1) as logf:
            logf.write("%s %s\n" % (time.ctime(), msg))
    except OSError:
        pass


def _kill_tree(pid: int):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True, timeout=30)


def _handler(control, event_type, data, ctx):
    if control == 1:  # SERVICE_CONTROL_STOP
        _stop_event.set()
        child = _child[0]
        if child is not None and child.poll() is None:
            _kill_tree(child.pid)


_handler_ref = HANDLER_EX(_handler)


def _report(ssh, state, controls=0, checkpoint=0, wait=5000):
    if ssh is None:
        return
    st = SERVICE_STATUS()
    st.dwServiceType = 0x10
    st.dwCurrentState = state
    st.dwControlsAccepted = controls
    st.dwWin32ExitCode = 0
    st.dwServiceSpecificExitCode = 0
    st.dwCheckPoint = checkpoint
    st.dwWaitHint = wait
    advapi32.SetServiceStatus(ssh, ctypes.byref(st))


def service_main(argc, argv):
    _log("service_main pid=%d" % os.getpid())

    try:
        advapi32.RegisterServiceCtrlHandlerExW.restype = ctypes.c_void_p
        advapi32.RegisterServiceCtrlHandlerExW.argtypes = [
            ctypes.c_wchar_p, HANDLER_EX, ctypes.c_void_p]
        ssh = advapi32.RegisterServiceCtrlHandlerExW(
            SERVICE_NAME, _handler_ref, None)
        if not ssh:
            _log("RegisterServiceCtrlHandlerEx failed err=%d" % kernel32.GetLastError())
            return
        _ssh[0] = ssh
        advapi32.SetServiceStatus.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(SERVICE_STATUS)]

        _report(ssh, 2)
        _log("reported START_PENDING")

        child = subprocess.Popen(
            [STRATA_PY] + _server_args(), cwd=STRATA_DIR,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        _child[0] = child
        _log("spawned server pid=%d config=%s" % (child.pid, _model_config()))

        _report(ssh, 4, controls=1)
        _log("reported RUNNING")

        while not _stop_event.is_set():
            if child.poll() is not None:
                _log("server exited rc=%s" % child.returncode)
                break
            time.sleep(1)

        if child.poll() is None:  # stopped via control handler already killed tree
            _kill_tree(child.pid)
            child.wait(timeout=60)
        _log("service stopping")
        _report(ssh, 3)
        time.sleep(0.5)
        _report(ssh, 1)
        _log("reported STOPPED")
    except Exception:
        _log("EXCEPTION:\n" + traceback.format_exc())
        _report(_ssh[0], 1)


_service_main_ref = SERVICE_MAIN(service_main)


def main():
    table = (SERVICE_TABLE_ENTRY * 2)()
    table[0].lpServiceName = SERVICE_NAME
    table[0].lpServiceProc = ctypes.cast(_service_main_ref, ctypes.c_void_p)
    table[1].lpServiceName = None
    table[1].lpServiceProc = None

    advapi32.StartServiceCtrlDispatcherW.argtypes = [
        ctypes.POINTER(SERVICE_TABLE_ENTRY)]
    advapi32.StartServiceCtrlDispatcherW.restype = wintypes.BOOL

    table_ptr = ctypes.cast(table, ctypes.POINTER(SERVICE_TABLE_ENTRY))
    result = advapi32.StartServiceCtrlDispatcherW(table_ptr)
    if not result:
        err = kernel32.GetLastError()
        _log("dispatcher failed err=%d, running in console mode" % err)
        service_main(0, None)


if __name__ == "__main__":
    main()
