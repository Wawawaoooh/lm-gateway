"""Desktop control panel for the persistent gateway services."""
from __future__ import annotations

import base64
import socket
import subprocess
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QComboBox, QFrame, QGridLayout,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QSpinBox, QTabWidget,
                             QTableView, QVBoxLayout, QWidget)

from ..config import GatewayConfig, load_config, save_config
from ..keystore import KeyStore
from ..limiter import RateLimiter
from ..logger import RequestLogger
from .dialogs import KeysDialog, UpstreamsDialog
from .widgets import RequestTableModel, ServiceCard, StatsBar, UsageTableModel

STYLE = """
QWidget {
    color: #dbe5f5;
    font-family: "Microsoft YaHei UI";
    font-size: 13px;
}
QWidget#app, QDialog { background: #0b1120; }
QLabel { background: transparent; }
QLabel#pageTitle { font-size: 24px; font-weight: 700; color: #f8fafc; }
QLabel#pageSubtitle, QLabel#serviceDescription, QLabel#metricTitle, QLabel#mutedLabel {
    color: #8291aa;
}
QLabel#sectionTitle { font-size: 16px; font-weight: 700; color: #f1f5f9; }
QLabel#serviceTitle { font-size: 16px; font-weight: 700; }
QLabel#metricValue { font-size: 20px; font-weight: 700; color: #f8fafc; }
QFrame#serviceCard, QFrame#metricCard, QFrame#settingsCard, QFrame#accessCard {
    background: #111a2e;
    border: 1px solid #24324b;
    border-radius: 10px;
}
QLabel#statusRunning, QLabel#statusStopped, QLabel#statusUnknown {
    border-radius: 11px;
    padding: 5px 12px;
    min-height: 16px;
    font-size: 12px;
    font-weight: 700;
}
QLabel#statusRunning { background: #123c32; color: #4ade80; }
QLabel#statusStopped { background: #431d2b; color: #fb7185; }
QLabel#statusUnknown { background: #3a3218; color: #facc15; }
QComboBox {
    background: #0d1627;
    border: 1px solid #30415f;
    border-radius: 7px;
    padding: 8px 12px;
    color: #dbe5f5;
    font-size: 14px;
    font-weight: 600;
    min-height: 20px;
}
QComboBox:hover { border-color: #466082; background: #101b30; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #8291aa;
    margin-right: 12px;
}
QComboBox QAbstractItemView {
    background: #17233a;
    color: #dbe5f5;
    border: 1px solid #30415f;
    border-radius: 6px;
    selection-background-color: #1e40af;
    selection-color: white;
    outline: none;
    font-size: 13px;
    font-weight: 400;
    padding: 4px;
}
QLabel#mountLabel { color: #4ade80; font-size: 12px; font-weight: 600; }
QPushButton#modeButton {
    padding: 6px 14px; border-radius: 7px; font-size: 12px;
    background: #0d1627; border: 1px solid #30415f; color: #8291aa;
}
QPushButton#modeButton:hover { border-color: #466082; }
QPushButton#modeButton:checked { background: #1e40af; color: white; border-color: #1e40af; font-weight: 600; }
QPushButton {
    background: #1b2942;
    border: 1px solid #30415f;
    border-radius: 7px;
    padding: 7px 14px;
    color: #dbe5f5;
}
QPushButton:hover { background: #253653; border-color: #466082; }
QPushButton:pressed { background: #152238; }
QPushButton:disabled { color: #526078; background: #121a2a; border-color: #202b40; }
QPushButton#primaryButton { background: #2563eb; border-color: #3b82f6; color: white; }
QPushButton#primaryButton:hover { background: #3475f5; }
QPushButton#primaryButton:disabled { color: #526078; background: #121a2a; border-color: #202b40; }
QPushButton#dangerButton { color: #fda4af; }
QLineEdit, QSpinBox {
    background: #0d1627;
    border: 1px solid #30415f;
    border-radius: 7px;
    padding: 7px 10px;
    min-height: 18px;
    selection-background-color: #2563eb;
}
QLineEdit:focus, QSpinBox:focus { border-color: #3b82f6; }
QTabWidget::pane { border: 1px solid #24324b; border-radius: 8px; background: #111a2e; }
QTabBar::tab { background: #0d1627; color: #8291aa; padding: 9px 18px; margin-right: 2px; }
QTabBar::tab:selected { background: #17233a; color: #f8fafc; }
QTableView, QTableWidget {
    background: #111a2e;
    alternate-background-color: #0f182a;
    border: none;
    gridline-color: #1e2a40;
    selection-background-color: #1e40af;
    selection-color: white;
}
QHeaderView::section {
    background: #17233a;
    color: #9fb0c9;
    border: none;
    border-bottom: 1px solid #2a3954;
    padding: 9px 6px;
    font-weight: 600;
}
QScrollBar:vertical { background: #0d1627; width: 10px; }
QScrollBar::handle:vertical { background: #30415f; border-radius: 5px; min-height: 30px; }
QMessageBox { background: #111a2e; }
"""


class MainWindow(QWidget):
    service_action_finished = pyqtSignal(str, str, bool)
    refresh_ready = pyqtSignal(dict)  # emitted from the refresh worker thread

    def __init__(self, cfg: GatewayConfig, keystore: KeyStore,
                 logger: RequestLogger, limiter: RateLimiter):
        super().__init__()
        self.setWindowTitle("LM Gateway 控制台")
        self.setObjectName("app")
        self.cfg = cfg
        self.ui_ks = keystore
        self.ui_log = logger
        self.limiter = limiter
        self._refreshing = False
        self.service_action_finished.connect(self._service_action_done)
        self.refresh_ready.connect(self._apply_refresh)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("LM Gateway")
        title.setObjectName("pageTitle")
        subtitle = QLabel("本地模型网关、远程隧道与 API 用量控制台")
        subtitle.setObjectName("pageSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        header.addStretch(1)
        self.overall_status = QLabel("检测中")
        self.overall_status.setObjectName("statusUnknown")
        self.overall_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.overall_status)
        refresh_button = QPushButton("立即刷新")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(refresh_button)
        root.addLayout(header)

        services_title = QLabel("服务控制")
        services_title.setObjectName("sectionTitle")
        root.addWidget(services_title)

        service_grid = QGridLayout()
        service_grid.setContentsMargins(0, 0, 0, 0)
        service_grid.setHorizontalSpacing(12)
        self.gateway_card = ServiceCard("lm-gateway", "网关服务", "监听本地端口并转发 OpenAI 兼容请求")
        self.tunnel_card = ServiceCard("cf-lm-gw", "Cloudflare 隧道", "将固定域名安全映射到本地网关")
        self.strata_card = ServiceCard("strata", "Strata 加速器", "Qwen3.8-Flash-Next 125B · 停止即卸载模型释放内存/显存")

        switcher = QWidget()
        switch_layout = QVBoxLayout(switcher)
        switch_layout.setContentsMargins(0, 0, 0, 0)
        switch_layout.setSpacing(6)
        self.strata_model_label = QLabel("当前挂载：—")
        self.strata_model_label.setObjectName("mountLabel")
        switch_layout.addWidget(self.strata_model_label)
        switch_row = QHBoxLayout()
        switch_row.setSpacing(8)
        self.model_combo = QComboBox()
        switch_row.addWidget(self.model_combo, 1)
        self.switch_button = QPushButton("切换")
        self.switch_button.setObjectName("primaryButton")
        self.switch_button.clicked.connect(self._switch_strata_model)
        switch_row.addWidget(self.switch_button)
        switch_layout.addLayout(switch_row)
        self.strata_card.add_extra(switcher)

        self.gateway_card.action_requested.connect(self._service_action)
        self.tunnel_card.action_requested.connect(self._service_action)
        self.strata_card.action_requested.connect(self._service_action)
        service_grid.addWidget(self.gateway_card, 0, 0)
        service_grid.addWidget(self.tunnel_card, 0, 1)
        service_grid.addWidget(self.strata_card, 0, 2)
        root.addLayout(service_grid)

        stats_title = QLabel("实时指标")
        stats_title.setObjectName("sectionTitle")
        root.addWidget(stats_title)
        self.stats = StatsBar()
        root.addWidget(self.stats)

        content = QHBoxLayout()
        content.setSpacing(12)

        settings = QFrame()
        settings.setObjectName("settingsCard")
        settings.setFixedWidth(310)
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(18, 16, 18, 16)
        settings_layout.setSpacing(10)
        settings_title = QLabel("网关设置")
        settings_title.setObjectName("sectionTitle")
        settings_layout.addWidget(settings_title)
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        self.host_edit = QLineEdit(cfg.bind_host)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(cfg.port)
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 512)
        self.concurrency_spin.setValue(cfg.max_concurrency)
        form.addWidget(QLabel("绑定地址"), 0, 0)
        form.addWidget(self.host_edit, 0, 1)
        form.addWidget(QLabel("监听端口"), 1, 0)
        form.addWidget(self.port_spin, 1, 1)
        form.addWidget(QLabel("最大并发"), 2, 0)
        form.addWidget(self.concurrency_spin, 2, 1)
        settings_layout.addLayout(form)
        save_button = QPushButton("保存配置")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self._save_settings)
        settings_layout.addWidget(save_button)
        hint = QLabel("配置保存后，重启网关服务生效。")
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        settings_layout.addWidget(hint)
        settings_layout.addSpacing(8)
        keys_button = QPushButton("API 密钥管理")
        routes_button = QPushButton("上游与模型路由")
        keys_button.clicked.connect(self.open_keys)
        routes_button.clicked.connect(self.open_upstreams)
        settings_layout.addWidget(keys_button)
        settings_layout.addWidget(routes_button)
        settings_layout.addStretch(1)
        content.addWidget(settings)

        tabs = QTabWidget()
        self.request_model = RequestTableModel()
        self.request_view = QTableView()
        self._setup_table(
            self.request_view, self.request_model,
            widths={0: 130, 1: 110, 2: 60, 5: 90, 6: 80, 7: 80, 8: 90, 9: 80, 10: 70},
            stretch_columns=(3, 4))
        tabs.addTab(self.request_view, "请求日志")

        self.usage_model = UsageTableModel(pricing=cfg.pricing)
        self.usage_view = QTableView()
        self._setup_table(
            self.usage_view, self.usage_model,
            widths={0: 60, 3: 90, 4: 100, 5: 100, 6: 100, 7: 130, 8: 110},
            stretch_columns=(1, 2))
        usage_page = QWidget()
        usage_layout = QVBoxLayout(usage_page)
        usage_layout.setContentsMargins(0, 0, 0, 0)
        usage_layout.setSpacing(6)
        mode_row = QHBoxLayout()
        self.cost_hint = QLabel("电费口径：按本机功耗与生成速度折算的边际电费")
        self.cost_hint.setObjectName("mutedLabel")
        mode_row.addWidget(self.cost_hint)
        mode_row.addStretch(1)
        self.cost_mode_elec = QPushButton("电费口径")
        self.cost_mode_api = QPushButton("等效 API 价")
        for button in (self.cost_mode_elec, self.cost_mode_api):
            button.setObjectName("modeButton")
            button.setCheckable(True)
        self.cost_mode_elec.setChecked(True)
        self.cost_mode_elec.clicked.connect(lambda: self._set_cost_mode("electricity"))
        self.cost_mode_api.clicked.connect(lambda: self._set_cost_mode("api"))
        mode_row.addWidget(self.cost_mode_elec)
        mode_row.addWidget(self.cost_mode_api)
        usage_layout.addLayout(mode_row)
        usage_layout.addWidget(self.usage_view, 1)
        tabs.addTab(usage_page, "Token 用量")
        content.addWidget(tabs, 1)
        root.addLayout(content, 1)

        access = QFrame()
        access.setObjectName("accessCard")
        access_layout = QHBoxLayout(access)
        access_layout.setContentsMargins(16, 12, 16, 12)
        access_layout.addWidget(QLabel("远程 API"))
        self.remote_url = QLineEdit(f"{cfg.public_url.rstrip('/')}/v1" if cfg.public_url
                                    else "http://127.0.0.1:8787/v1")
        self.remote_url.setReadOnly(True)
        access_layout.addWidget(self.remote_url, 1)
        copy_button = QPushButton("复制地址")
        copy_button.clicked.connect(self._copy_url)
        access_layout.addWidget(copy_button)
        self.endpoint_status = QLabel("检测中")
        self.endpoint_status.setObjectName("statusUnknown")
        access_layout.addWidget(self.endpoint_status)
        root.addWidget(access)

        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.refresh()
        self.timer.start()

    @staticmethod
    def _setup_table(view: QTableView, model, widths: dict | None = None,
                     stretch_columns: tuple = ()):
        view.setModel(model)
        view.setAlternatingRowColors(True)
        view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        view.verticalHeader().setVisible(False)
        view.verticalHeader().setDefaultSectionSize(34)
        header = view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        for column, width in (widths or {}).items():
            view.setColumnWidth(column, width)
        for column in stretch_columns:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)

    @staticmethod
    def _service_status(name: str) -> str:
        try:
            result = subprocess.run(["sc.exe", "query", name], capture_output=True,
                                    text=True, errors="replace", timeout=3,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        if result.returncode != 0:
            return "not installed"
        output = result.stdout.upper()
        if "RUNNING" in output:
            return "running"
        if "STOPPED" in output:
            return "stopped"
        if "START_PENDING" in output:
            return "正在启动"
        if "STOP_PENDING" in output:
            return "正在停止"
        return "unknown"

    @staticmethod
    def _port_open(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            return False

    def refresh(self):
        if self._refreshing:  # one worker at a time; UI thread stays responsive
            return
        self._refreshing = True
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        data: dict = {
            "gateway": self._service_status("lm-gateway"),
            "tunnel": self._service_status("cf-lm-gw"),
            "strata": self._service_status("strata"),
        }
        data["gateway_online"] = (data["gateway"] == "running"
                                  and self._port_open("127.0.0.1", self.cfg.port))
        try:
            data["stats"] = self.ui_log.stats()
            data["recent"] = self.ui_log.recent()
            data["usage"] = self.ui_log.key_usage()
            data["key_model_usage"] = self.ui_log.key_model_usage()
        except Exception as exc:  # noqa: BLE001
            data["db_error"] = f"{type(exc).__name__}: {exc}"
        self.refresh_ready.emit(data)

    def _apply_refresh(self, data: dict):
        self._refreshing = False
        gateway_state = data["gateway"]
        tunnel_state = data["tunnel"]
        self.gateway_card.set_status(gateway_state)
        self.tunnel_card.set_status(tunnel_state)
        self.strata_card.set_status(data.get("strata", "stopped"))
        self._sync_model_combo()

        all_online = data["gateway_online"] and tunnel_state == "running"
        self._set_pill(self.overall_status, "系统正常" if all_online else "需要检查", all_online)
        self._set_pill(self.endpoint_status, "在线" if all_online else "离线", all_online)

        if "db_error" in data:
            self._set_pill(self.overall_status, "数据读取失败", False)
            return
        self.stats.update_stats(data["stats"])
        for model, view, rows in ((self.request_model, self.request_view, data["recent"]),
                                  (self.usage_model, self.usage_view, data["usage"])):
            scroll = view.verticalScrollBar().value()
            if model is self.usage_model:
                model.refresh(rows, data.get("key_model_usage"))
            else:
                model.refresh(rows)
            view.verticalScrollBar().setValue(scroll)

    def _set_cost_mode(self, mode: str):
        self.usage_model.set_mode(mode)
        self.cost_mode_elec.setChecked(mode == "electricity")
        self.cost_mode_api.setChecked(mode == "api")
        self.cost_hint.setText(
            "电费口径：按本机功耗与生成速度折算的边际电费" if mode == "electricity"
            else "等效 API 价：按阿里云百炼牌价折算的等价调用成本")

    @staticmethod
    def _set_pill(label: QLabel, text: str, healthy: bool):
        label.setText(text)
        label.setObjectName("statusRunning" if healthy else "statusStopped")
        label.style().unpolish(label)
        label.style().polish(label)

    def _service_action(self, service_name: str, action: str):
        cards = {"lm-gateway": self.gateway_card,
                 "cf-lm-gw": self.tunnel_card,
                 "strata": self.strata_card}
        card = cards.get(service_name)
        if card is None:
            return
        card.set_busy()
        threading.Thread(target=self._run_service_action,
                         args=(service_name, action), daemon=True).start()

    def _run_service_action(self, service_name: str, action: str):
        command = {"start": "Start-Service", "stop": "Stop-Service",
                   "restart": "Restart-Service"}[action]
        elevated = f"{command} -Name '{service_name}' -ErrorAction Stop"
        encoded = base64.b64encode(elevated.encode("utf-16le")).decode("ascii")
        launcher = (
            "Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList "
            f"'-NoProfile','-EncodedCommand','{encoded}'")
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-Command", launcher],
                                    capture_output=True, timeout=45,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            success = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            success = False
        self.service_action_finished.emit(service_name, action, success)

    def _service_action_done(self, service_name: str, action: str, success: bool):
        QTimer.singleShot(800, self.refresh)
        if not success:
            QMessageBox.warning(self, "操作未完成", f"{service_name} 的 {action} 操作失败或被取消。")

    STRATA_DIR = Path(r"F:\Peterpertrilli\Strata")
    STRATA_MODEL_INFO = {
        "iq2_xs": "速度优先 · 实测约 59 t/s",
        "iq3_s": "质量追平原版 · 实测约 42 t/s",
        "q2_0": "最快档 · 官方约 70 t/s",
        "iq3_xxs": "高画质 · 官方约 46 t/s",
        "coder": "代码特化 IQ1_M · 官方约 45 t/s",
    }

    def _installed_strata_models(self) -> list[str]:
        configs = sorted(self.STRATA_DIR.glob("strata-*.json"))
        return [p.stem.removeprefix("strata-") for p in configs]

    def _sync_model_combo(self):
        models = self._installed_strata_models()
        if not models:
            return
        pointer = self.STRATA_DIR / "service-model.txt"
        current = pointer.read_text(encoding="utf-8").strip() if pointer.exists() else models[0]
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for name in models:
            hint = self.STRATA_MODEL_INFO.get(name)
            text = f"{name.upper()} — {hint}" if hint else name
            self.model_combo.addItem(text, userData=name)
        index = self.model_combo.findData(current)
        self.model_combo.setCurrentIndex(max(index, 0))
        self.model_combo.blockSignals(False)
        hint = self.STRATA_MODEL_INFO.get(current)
        self.strata_model_label.setText(
            f"当前挂载：Qwen3.8-Flash-Next 125B · {current.upper()}"
            + (f"（{hint}）" if hint else ""))

    def _switch_strata_model(self):
        target = (self.model_combo.currentData() or self.model_combo.currentText()).strip()
        if not target:
            return
        pointer = self.STRATA_DIR / "service-model.txt"
        try:
            pointer.write_text(target + "\n", encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "切换失败", f"写入模型指针失败：{exc}")
            return
        self.strata_card.set_busy()
        self.switch_button.setEnabled(False)
        threading.Thread(target=self._run_strata_switch, daemon=True).start()

    def _run_strata_switch(self):
        script = ("$s = Get-Service -Name strata\n"
                  "if ($s.Status -ne 'Running') { Start-Service -Name strata -ErrorAction Stop }\n"
                  "else { Restart-Service -Name strata -ErrorAction Stop }\n"
                  "$g = Get-Service -Name lm-gateway\n"
                  "if ($g.Status -eq 'Running') { Restart-Service -Name lm-gateway -ErrorAction Stop }\n")
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        launcher = (
            "Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList "
            f"'-NoProfile','-EncodedCommand','{encoded}'")
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-Command", launcher],
                                    capture_output=True, timeout=60,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            success = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            success = False
        self.switch_button.setEnabled(True)
        self.service_action_finished.emit("strata", "restart", success)

    def _save_settings(self):
        self.cfg.bind_host = self.host_edit.text().strip() or "127.0.0.1"
        self.cfg.port = self.port_spin.value()
        self.cfg.max_concurrency = self.concurrency_spin.value()
        try:
            save_config(self.cfg)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        QMessageBox.information(self, "配置已保存", "新配置将在下次重启网关服务后生效。")

    def open_keys(self):
        KeysDialog(self.ui_ks, sorted(self.cfg.model_routes), parent=self).exec()
        self.refresh()

    def open_upstreams(self):
        dialog = UpstreamsDialog(self.cfg, parent=self)
        dialog.exec()
        self.cfg = load_config()
        self.host_edit.setText(self.cfg.bind_host)
        self.port_spin.setValue(self.cfg.port)
        self.concurrency_spin.setValue(self.cfg.max_concurrency)
        self.refresh()

    def _copy_url(self):
        QApplication.clipboard().setText(self.remote_url.text())
        self.endpoint_status.setText("已复制")
        QTimer.singleShot(1200, self.refresh)

    def closeEvent(self, event):  # noqa: N802
        self.timer.stop()
        super().closeEvent(event)


def main():
    import faulthandler
    import sys
    crash_log = Path(r"F:\cloudflare\gui_crash.log")
    crash_log.parent.mkdir(parents=True, exist_ok=True)
    faulthandler.enable(open(str(crash_log), "a"))

    def _hook(exc_type, exc_value, tb):
        import traceback
        with open(str(crash_log), "a", encoding="utf-8") as logf:
            logf.write("=== %s ===\n" % time.ctime())
            logf.write("".join(traceback.format_exception(exc_type, exc_value, tb)))
    sys.excepthook = _hook

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow(load_config(), KeyStore(), RequestLogger(), RateLimiter())
    window.resize(1280, 800)
    window.setMinimumSize(1080, 700)
    window.show()
    sys.exit(app.exec())
