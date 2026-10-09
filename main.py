"""LM Gateway CLI management tool."""
from __future__ import annotations

import argparse
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    os.system("")  # enable VT100 escape sequences
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "gateway.db"
CONFIG_PATH = ROOT / "config.json"
PYTHON = sys.executable

# ── helpers ──────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _service_status(name: str) -> str:
    try:
        out = subprocess.check_output(
            ["sc.exe", "query", name], text=True, stderr=subprocess.DEVNULL, timeout=5)
        for line in out.splitlines():
            line = line.strip()
            if "STATE" in line and "RUNNING" in line:
                return "running"
            if "STATE" in line and "STOPPED" in line:
                return "stopped"
            if "STATE" in line:
                return line.split()[-1].lower()
        return "unknown"
    except Exception:
        return "not installed"


def _elevate(args: list[str]) -> int:
    try:
        subprocess.run(
            ["powershell", "-Command",
             f"Start-Process -Verb RunAs -FilePath '{args[0]}' -ArgumentList '{' '.join(args[1:])}' -Wait"],
            timeout=30)
        return 0
    except Exception as e:
        print(f"  提权失败: {e}")
        return 1


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _check_http(url: str, timeout: float = 2.0) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, "ok"
    except urllib.error.HTTPError as e:
        return e.code, f"HTTP {e.code}"
    except Exception as e:
        return 0, str(e)[:40]


# ── subcommands ──────────────────────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> None:
    cfg = _load_config()
    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║         LM Gateway  系统状态             ║")
    print("  ╚══════════════════════════════════════════╝")
    print()

    # services
    gw_state = _service_status("lm-gateway")
    cf_state = _service_status("cf-lm-gw")
    st_state = _service_status("strata")
    gw_color = "✓" if gw_state == "running" else "✗"
    cf_color = "✓" if cf_state == "running" else "✗"
    st_color = "✓" if st_state == "running" else "✗"

    print(f"  服务状态")
    print(f"  ├─ 网关服务 (lm-gateway)     : {gw_color} {gw_state}")
    print(f"  ├─ 隧道服务 (cf-lm-gw)       : {cf_color} {cf_state}")
    print(f"  ├─ 加速器 (strata 125B)      : {st_color} {st_state}")

    # ports
    port = cfg.get("port", 8791)
    host = cfg.get("bind_host", "127.0.0.1")
    gw_port = _port_open(host, port)
    lms_port = _port_open("127.0.0.1", 1234)
    ollama_port = _port_open("127.0.0.1", 11434)

    print()
    print(f"  端口监听")
    print(f"  ├─ 网关 :{port}              : {'✓ 开放' if gw_port else '✗ 关闭'}")
    print(f"  ├─ LM Studio :1234           : {'✓ 开放' if lms_port else '✗ 关闭'}")
    print(f"  └─ Ollama :11434             : {'✓ 开放' if ollama_port else '✗ 关闭'}")

    # upstream health
    print()
    print(f"  上游健康")
    for up in cfg.get("upstreams", []):
        base = up["base_url"]
        code, msg = _check_http(f"{base}/models")
        icon = "✓" if code in (200, 401, 403, 404) else "✗"
        print(f"  ├─ {up['name']:<20s} : {icon} {msg}")

    # tunnel endpoint
    if cfg.public_url:
        print()
        code, msg = _check_http(f"{cfg.public_url.rstrip('/')}/v1/models")
        icon = "✓" if code in (200, 401, 403) else "✗"
        print(f"  远程端点")
        print(f"  └─ {cfg.public_url:<27s}: {icon} {msg}")

    # quick stats
    if DB_PATH.exists():
        try:
            conn = _db_conn()
            s = conn.execute(
                "SELECT COUNT(*), SUM(prompt_tokens), SUM(completion_tokens) "
                "FROM requests WHERE ts>?", (time.time() - 86400,)).fetchone()
            reqs = s[0] or 0
            ptok = s[1] or 0
            ctok = s[2] or 0
            keys = conn.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
            routes = len(cfg.get("model_routes", {}))
            conn.close()
            print()
            print(f"  24h 概览")
            print(f"  ├─ 请求数   : {reqs}")
            print(f"  ├─ Token入  : {_fmt_tokens(ptok)}")
            print(f"  ├─ Token出  : {_fmt_tokens(ctok)}")
            print(f"  ├─ API 密钥 : {keys}")
            print(f"  └─ 模型路由 : {routes}")
        except Exception:
            pass

    print()


def cmd_start(args: argparse.Namespace) -> None:
    target = args.target or "all"
    if target in ("gateway", "all"):
        print("  启动网关服务...")
        st = _service_status("lm-gateway")
        if st == "running":
            print("  网关服务已在运行中")
        else:
            _elevate(["sc.exe", "start", "lm-gateway"])
            time.sleep(2)
            st = _service_status("lm-gateway")
            print(f"  网关服务: {st}")
    if target in ("tunnel", "all"):
        print("  启动隧道服务...")
        st = _service_status("cf-lm-gw")
        if st == "running":
            print("  隧道服务已在运行中")
        else:
            _elevate(["sc.exe", "start", "cf-lm-gw"])
            time.sleep(2)
            st = _service_status("cf-lm-gw")
            print(f"  隧道服务: {st}")
    if target == "strata":
        print("  启动加速器服务（模型加载约 1-3 分钟）...")
        st = _service_status("strata")
        if st == "running":
            print("  加速器服务已在运行中")
        else:
            _elevate(["sc.exe", "start", "strata"])
            time.sleep(2)
            st = _service_status("strata")
            print(f"  加速器服务: {st}")
    print()


def cmd_stop(args: argparse.Namespace) -> None:
    target = args.target or "all"
    if target in ("gateway", "all"):
        print("  停止网关服务...")
        _elevate(["sc.exe", "stop", "lm-gateway"])
        time.sleep(2)
        st = _service_status("lm-gateway")
        print(f"  网关服务: {st}")
    if target in ("tunnel", "all"):
        print("  停止隧道服务...")
        _elevate(["sc.exe", "stop", "cf-lm-gw"])
        time.sleep(2)
        st = _service_status("cf-lm-gw")
        print(f"  隧道服务: {st}")
    if target in ("strata", "all"):
        print("  停止加速器服务（卸载模型，释放内存/显存）...")
        _elevate(["sc.exe", "stop", "strata"])
        time.sleep(2)
        st = _service_status("strata")
        print(f"  加速器服务: {st}")
    print()


def cmd_restart(args: argparse.Namespace) -> None:
    target = args.target or "gateway"
    print(f"  重启{target}服务...")
    if target in ("gateway", "all"):
        _elevate(["sc.exe", "stop", "lm-gateway"])
        time.sleep(3)
        _elevate(["sc.exe", "start", "lm-gateway"])
        time.sleep(2)
        print(f"  网关服务: {_service_status('lm-gateway')}")
    if target in ("tunnel", "all"):
        _elevate(["sc.exe", "stop", "cf-lm-gw"])
        time.sleep(3)
        _elevate(["sc.exe", "start", "cf-lm-gw"])
        time.sleep(2)
        print(f"  隧道服务: {_service_status('cf-lm-gw')}")
    if target == "strata":
        print("  重启加速器（模型重新加载约 1-3 分钟）...")
        _elevate(["sc.exe", "stop", "strata"])
        time.sleep(3)
        _elevate(["sc.exe", "start", "strata"])
        time.sleep(2)
        print(f"  加速器服务: {_service_status('strata')}")
    print()


def cmd_usage(args: argparse.Namespace) -> None:
    if not DB_PATH.exists():
        print("  数据库不存在，尚无用量数据")
        return
    conn = _db_conn()
    rows = conn.execute(
        """SELECT k.id, k.name, k.prefix,
                  COUNT(r.ts) AS reqs,
                  COALESCE(SUM(r.prompt_tokens),0) AS ptok,
                  COALESCE(SUM(r.completion_tokens),0) AS ctok,
                  COALESCE(SUM(r.prompt_tokens + r.completion_tokens),0) AS total
           FROM keys k LEFT JOIN requests r ON r.key_id = k.id
           GROUP BY k.id ORDER BY total DESC"""
    ).fetchall()

    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║           API Key Token 用量             ║")
    print("  ╚══════════════════════════════════════════╝")
    print()
    header = f"  {'ID':<4} {'名称':<18} {'前缀':<14} {'请求':>7} {'入Token':>10} {'出Token':>10} {'总计':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    total_reqs = 0
    total_ptok = 0
    total_ctok = 0
    for r in rows:
        print(f"  {r[0]:<4} {str(r[1]):<18} {str(r[2]):<14} {r[3] or 0:>7,} "
              f"{_fmt_tokens(r[4]):>10} {_fmt_tokens(r[5]):>10} {_fmt_tokens(r[6]):>10}")
        total_reqs += r[3] or 0
        total_ptok += r[4] or 0
        total_ctok += r[5] or 0
    print("  " + "-" * (len(header) - 2))
    print(f"  {'':<4} {'合计':<18} {'':<14} {total_reqs:>7,} "
          f"{_fmt_tokens(total_ptok):>10} {_fmt_tokens(total_ctok):>10} "
          f"{_fmt_tokens(total_ptok + total_ctok):>10}")
    print()

    # 24h detail
    s = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
        "COALESCE(AVG(latency_ms),0) FROM requests WHERE ts>?", (time.time() - 86400,)).fetchone()
    print(f"  24h 汇总: {s[0]} 请求 | 入 {_fmt_tokens(s[1])} | 出 {_fmt_tokens(s[2])} | 平均延迟 {s[3]:.0f}ms")

    errs = conn.execute("SELECT COUNT(*) FROM requests WHERE ts>? AND status>=400",
                        (time.time() - 86400,)).fetchone()[0]
    print(f"  24h 错误: {errs}")
    conn.close()
    print()


def cmd_keys(args: argparse.Namespace) -> None:
    if not DB_PATH.exists():
        print("  数据库不存在")
        return
    conn = _db_conn()
    rows = conn.execute(
        "SELECT id, name, prefix, rate_limit_rpm, models, enabled, "
        "datetime(created_at, 'unixepoch', 'localtime') FROM keys ORDER BY id"
    ).fetchall()
    conn.close()

    print()
    print("  API 密钥列表")
    print(f"  {'ID':<4} {'名称':<18} {'前缀':<14} {'限速(rpm)':>10} {'模型':<20} {'状态':<6} {'创建时间'}")
    print("  " + "-" * 100)
    for r in rows:
        rpm = str(r[3]) if r[3] else "无限制"
        models = (r[4] or "全部")[:20]
        status = "启用" if r[5] else "禁用"
        created = r[6] or "—"
        print(f"  {r[0]:<4} {r[1]:<18} {r[2]:<14} {rpm:>10} {models:<20} {status:<6} {created}")
    print()


def cmd_logs(args: argparse.Namespace) -> None:
    if not DB_PATH.exists():
        print("  数据库不存在")
        return
    conn = _db_conn()
    limit = args.limit or 20
    rows = conn.execute(
        """SELECT datetime(ts, 'unixepoch', 'localtime'), ip, key_id, model,
                  latency_ms, prompt_tokens, completion_tokens, status
           FROM requests ORDER BY ts DESC LIMIT ?""", (limit,)
    ).fetchall()
    conn.close()

    print()
    print(f"  最近 {limit} 条请求")
    print(f"  {'时间':<20} {'IP':<16} {'Key':>4} {'模型':<30} {'延迟':>7} {'入':>7} {'出':>7} {'状态':>5}")
    print("  " + "-" * 105)
    for r in rows:
        ts = r[0] or "—"
        ip = (r[1] or "")[:16]
        kid = r[2] if r[2] else "-"
        model = (r[3] or "")[:30]
        lat = f"{r[4]:.0f}ms" if r[4] else "—"
        pt = _fmt_tokens(r[5] or 0)
        ct = _fmt_tokens(r[6] or 0)
        st = r[7] if r[7] else "-"
        print(f"  {ts:<20} {ip:<16} {kid:>4} {model:<30} {lat:>7} {pt:>7} {ct:>7} {st:>5}")
    print()


def cmd_gui(args: argparse.Namespace) -> None:
    from gateway.ui.main_window import main as gui_main
    gui_main()


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lm-gateway",
        description="LM Gateway 管理工具")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="查看系统状态")

    p_start = sub.add_parser("start", help="启动服务")
    p_start.add_argument("target", nargs="?", choices=["gateway", "tunnel", "strata", "all"],
                         default="all", help="目标服务 (默认: all)")

    p_stop = sub.add_parser("stop", help="停止服务")
    p_stop.add_argument("target", nargs="?", choices=["gateway", "tunnel", "strata", "all"],
                        default="all", help="目标服务 (默认: all)")

    p_restart = sub.add_parser("restart", help="重启服务")
    p_restart.add_argument("target", nargs="?", choices=["gateway", "tunnel", "strata", "all"],
                           default="gateway", help="目标服务 (默认: gateway)")

    sub.add_parser("usage", help="Token 用量统计")
    sub.add_parser("keys", help="查看 API 密钥")

    p_logs = sub.add_parser("logs", help="查看最近请求")
    p_logs.add_argument("-n", "--limit", type=int, default=20, help="显示条数 (默认: 20)")

    sub.add_parser("gui", help="启动图形界面")

    args = parser.parse_args()

    if args.command is None:
        cmd_status(args)
        return

    dispatch = {
        "status": cmd_status,
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "usage": cmd_usage,
        "keys": cmd_keys,
        "logs": cmd_logs,
        "gui": cmd_gui,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
