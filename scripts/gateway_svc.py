"""Windows service for the LM gateway via ctypes (no pywin32).

Runs as:  C:\Python314\python.exe F:\Peterpertrilli\scripts\gateway_svc.py
Also runs in a plain console for testing (dispatcher fails, falls back to direct run).
"""
import ctypes
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, r"F:\Peterpertrilli\.venv\Lib\site-packages")

LOG_PATH = r"F:\cloudflare\gw_service.log"

advapi32 = ctypes.windll.advapi32
kernel32 = ctypes.windll.kernel32

SERVICE_NAME = "lm-gateway"


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
    None, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID)
SERVICE_MAIN = ctypes.WINFUNCTYPE(
    None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))

_server = [None]
_ssh = [None]
_stop_event = threading.Event()


def _handler(control, event_type, data, ctx):
    if control == 1:
        _stop_event.set()
        srv = _server[0]
        if srv is not None:
            srv.should_exit = True


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
    logf = open(LOG_PATH, "a", buffering=1)
    sys.stdout = sys.stderr = logf

    try:
        logf.write("%s service_main pid=%d\n" % (time.ctime(), os.getpid()))

        import uvicorn
        from gateway.config import load_config
        from gateway.keystore import KeyStore
        from gateway.limiter import RateLimiter
        from gateway.logger import RequestLogger
        from gateway.proxy import create_app

        advapi32.RegisterServiceCtrlHandlerExW.restype = ctypes.c_void_p
        advapi32.RegisterServiceCtrlHandlerExW.argtypes = [
            ctypes.c_wchar_p, HANDLER_EX, ctypes.c_void_p]
        ssh = advapi32.RegisterServiceCtrlHandlerExW(
            SERVICE_NAME, _handler_ref, None)
        if not ssh:
            logf.write("RegisterServiceCtrlHandlerEx failed err=%d\n" %
                        kernel32.GetLastError())
            return
        _ssh[0] = ssh

        advapi32.SetServiceStatus.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(SERVICE_STATUS)]

        _report(ssh, 2)
        logf.write("reported START_PENDING\n")

        cfg = load_config()
        limiter = RateLimiter()
        limiter.configure(int(cfg.max_concurrency))
        app = create_app(cfg, KeyStore(), RequestLogger(), limiter)
        config = uvicorn.Config(
            app, host=cfg.bind_host, port=int(cfg.port), log_level="warning")
        server = uvicorn.Server(config)
        _server[0] = server

        _report(ssh, 4, controls=1)
        logf.write("reported RUNNING\n")

        server.run()

        logf.write("server exited\n")
        _report(ssh, 3)
        time.sleep(0.5)
        _report(ssh, 1)
        logf.write("reported STOPPED\n")
    except Exception:
        logf.write("EXCEPTION:\n" + traceback.format_exc() + "\n")
        _report(_ssh[0], 1)


_service_main_ref = SERVICE_MAIN(service_main)


def main():
    logf = open(LOG_PATH, "a", buffering=1)
    logf.write("%s main starting pid=%d\n" % (time.ctime(), os.getpid()))

    table = (SERVICE_TABLE_ENTRY * 2)()
    table[0].lpServiceName = SERVICE_NAME
    table[0].lpServiceProc = ctypes.cast(
        _service_main_ref, ctypes.c_void_p)
    table[1].lpServiceName = None
    table[1].lpServiceProc = None

    advapi32.StartServiceCtrlDispatcherW.argtypes = [
        ctypes.POINTER(SERVICE_TABLE_ENTRY)]
    advapi32.StartServiceCtrlDispatcherW.restype = wintypes.BOOL

    table_ptr = ctypes.cast(table, ctypes.POINTER(SERVICE_TABLE_ENTRY))
    result = advapi32.StartServiceCtrlDispatcherW(table_ptr)
    if not result:
        err = kernel32.GetLastError()
        logf.write("dispatcher failed err=%d, running in console mode\n" % err)
        logf.close()
        service_main(0, None)


if __name__ == "__main__":
    main()
