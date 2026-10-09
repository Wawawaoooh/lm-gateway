"""Dialogs: API-key management and upstream/model synchronisation."""
from __future__ import annotations

import threading

import httpx
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QSpinBox, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from ..config import GatewayConfig, Upstream, save_config, slug_for
from ..keystore import KeyStore


class KeysDialog(QDialog):
    def __init__(self, ks: KeyStore, model_ids: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("API 密钥管理")
        self.ks = ks
        self.resize(760, 520)
        root = QVBoxLayout(self)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(("启用", "名称", "前缀", "RPM限制", "模型白名单", "操作"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 2)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.rpm_check = QCheckBox("启用每分钟限额")
        self.rpm_spin = QSpinBox()
        self.rpm_spin.setRange(1, 100_000)
        self.rpm_spin.setValue(60)
        self.models_edit = QLineEdit()
        self.models_edit.setPlaceholderText("留空=全部；逗号分隔，如 " + ", ".join(model_ids[:2]))
        form.addRow("名称", self.name_edit)
        roww = QHBoxLayout()
        roww.addWidget(self.rpm_check)
        roww.addWidget(self.rpm_spin)
        form.addRow("速率限制", roww)
        form.addRow("模型白名单", self.models_edit)
        root.addLayout(form)

        create = QPushButton("生成密钥")
        create.clicked.connect(self._create)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        btns = QHBoxLayout()
        btns.addWidget(create)
        btns.addStretch(1)
        btns.addWidget(close)
        root.addLayout(btns)

        self.reload()

    def reload(self):
        rows = self.ks.list()
        self.table.setRowCount(len(rows))
        for i, k in enumerate(rows):
            chk = QCheckBox()
            chk.setChecked(k.enabled)
            chk.stateChanged.connect(lambda _s, kid=k.id, c=chk: self.ks.set_enabled(kid, c.isChecked()))
            wrap = QWidget()
            lay = QHBoxLayout(wrap)
            lay.setContentsMargins(4, 0, 4, 0)
            lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(chk)
            self.table.setCellWidget(i, 0, wrap)
            for col, val in ((1, k.name), (2, k.prefix),
                             (3, "不限" if k.rate_limit_rpm is None else str(k.rate_limit_rpm)),
                             (4, k.models or "全部")):
                self.table.setItem(i, col, QTableWidgetItem(str(val)))
            del_btn = QPushButton("删除")
            del_btn.clicked.connect(lambda _c=False, kid=k.id, nm=k.name: self._delete(kid, nm))
            self.table.setCellWidget(i, 5, del_btn)

    def _create(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入密钥名称")
            return
        rpm = self.rpm_spin.value() if self.rpm_check.isChecked() else None
        models = [m.strip() for m in self.models_edit.text().split(",") if m.strip()] or None
        try:
            info, token = self.ks.create(name, rate_rpm=rpm, models=models)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(exc))
            return
        self.name_edit.clear()
        self.reload()
        box = QMessageBox.information(
            self, "密钥已生成（仅此一次可见）",
            f"名称：{info.name}\n\n{token}\n\n请立即复制保存，关闭后无法再次查看。",
            QMessageBox.StandardButton.Ok)

    def _delete(self, key_id: int, name: str):
        if QMessageBox.question(self, "确认删除", f"确定删除密钥 {name} ？") == \
                QMessageBox.StandardButton.Yes:
            self.ks.revoke(key_id=key_id)
            self.reload()


class UpstreamsDialog(QDialog):
    sync_finished = pyqtSignal(int, list)  # found, failed — emitted from worker thread

    def __init__(self, cfg: GatewayConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("上游与模型路由")
        self.cfg = cfg
        self.resize(760, 540)
        root = QVBoxLayout(self)
        self.sync_finished.connect(self._sync_done)

        root.addWidget(QLabel("上游（OpenAI 兼容 API，例如 LM Studio / Ollama）"))
        self.up_table = QTableWidget()
        self.up_table.setColumnCount(3)
        self.up_table.setHorizontalHeaderLabels(("名称", "Base URL", "上游API Key"))
        self.up_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.up_table, 2)

        up_btns = QHBoxLayout()
        add_row = QPushButton("添加行")
        del_row = QPushButton("删除选中行")
        self.sync_btn = QPushButton("同步模型列表 →")
        add_row.clicked.connect(lambda: self._add_up_row())
        del_row.clicked.connect(self._del_up_row)
        self.sync_btn.clicked.connect(self._sync_models)
        up_btns.addWidget(add_row)
        up_btns.addWidget(del_row)
        up_btns.addStretch(1)
        up_btns.addWidget(self.sync_btn)
        root.addLayout(up_btns)

        self.model_table = QTableWidget()
        self.model_table.setColumnCount(2)
        self.model_table.setHorizontalHeaderLabels(("对外模型 ID（远程填这个）", "上游"))
        self.model_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.model_table, 2)

        btns = QHBoxLayout()
        save = QPushButton("保存并关闭")
        save.clicked.connect(self._save_close)
        btns.addStretch(1)
        btns.addWidget(save)
        root.addLayout(btns)

        self.reload_upstreams()
        self.reload_models()

    def reload_upstreams(self):
        ups = list(self.cfg.upstreams) + [Upstream("", "", None)]
        self.up_table.setRowCount(len(ups))
        for i, u in enumerate(ups):
            vals = (u.name, u.base_url, u.api_key or "") if u else ("", "", "")
            for col, val in enumerate(vals):
                self.up_table.setItem(i, col, QTableWidgetItem(val))

    def _add_up_row(self):
        r = self.up_table.rowCount()
        self.up_table.insertRow(r)
        for c in range(3):
            self.up_table.setItem(r, c, QTableWidgetItem(""))

    def _del_up_row(self):
        row = self.up_table.currentRow()
        if row < 0:
            return QMessageBox.information(self, "提示", "请先选中一行")
        self.up_table.removeRow(row)

    def collect_upstreams(self) -> list[Upstream]:
        out = []
        for r in range(self.up_table.rowCount()):
            cells = [self.up_table.item(r, c) for c in range(3)]
            vals = [(it.text().strip() if it else "") for it in cells]
            if vals[0] and vals[1]:
                out.append(Upstream(vals[0], vals[1].rstrip("/"), vals[2] or None))
        return out

    def _sync_models(self):
        ups = self.collect_upstreams()
        if not ups:
            QMessageBox.warning(self, "提示", "至少填写一个有效上游")
            return
        self.sync_btn.setEnabled(False)
        self.sync_btn.setText("同步中…")
        threading.Thread(target=self._sync_worker, args=(ups,), daemon=True).start()

    def _sync_worker(self, ups: list[Upstream]):
        found, failed = 0, []
        for u in ups:
            try:
                resp = httpx.get(u.base_url.rstrip("/") + "/models",
                                 headers={"authorization": f"Bearer {u.api_key}"} if u.api_key else {},
                                 timeout=8.0)
                ids = [m["id"] for m in resp.json().get("data", [])]
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{u.name}: {type(exc).__name__} {exc}")
                continue
            for mid in ids:
                self.cfg.model_routes[slug_for(u.name, mid)] = {"up": u.name, "model": mid}
                found += 1
        self.cfg.upstreams = ups
        self.sync_finished.emit(found, failed)

    def _sync_done(self, found: int, failed: list):
        save_config(self.cfg)
        self.sync_btn.setEnabled(True)
        self.sync_btn.setText("同步模型列表 →")
        self.reload_upstreams()
        self.reload_models()
        msg = f"同步完成，当前共 {found} 个模型（重复同步会覆盖路由表）。"
        if failed:
            QMessageBox.warning(self, "部分上游不可达", msg + "\n\n" + "\n".join(failed))
        else:
            QMessageBox.information(self, "同步完成", msg)

    def reload_models(self):
        routes = sorted(self.cfg.model_routes.items())
        self.model_table.setRowCount(len(routes))
        for i, (eid, v) in enumerate(routes):
            self.model_table.setItem(i, 0, QTableWidgetItem(eid))
            self.model_table.setItem(i, 1, QTableWidgetItem(v["up"]))

    def _save_close(self):
        ups = self.collect_upstreams()
        if not ups:
            QMessageBox.warning(self, "提示", "至少保留一个有效上游")
            return
        names = {u.name for u in ups}
        stale = [eid for eid, v in list(self.cfg.model_routes.items()) if v["up"] not in names]
        for eid in stale:
            self.cfg.model_routes.pop(eid, None)
        self.cfg.upstreams = ups
        save_config(self.cfg)
        self.accept()
