"""Dashboard widgets and table models."""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor
import fnmatch

from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout, QWidget)

REQUEST_COLUMNS = ("时间", "IP", "Key", "模型", "上游", "延迟", "输入", "输出", "速度", "缓存≈", "状态")
USAGE_COLUMNS = ("ID", "名称", "前缀", "请求数", "输入 Token", "输出 Token", "总 Token", "单价 ¥/1M", "花费")


def model_price(pricing: dict | None, model: str) -> tuple[float, float]:
    """Resolve (input, output) price in ¥ per 1M tokens for a model id."""
    default = (4.0, 16.0)
    if not isinstance(pricing, dict):
        return default
    for pattern, p in pricing.items():
        if pattern != "default" and fnmatch.fnmatch(model or "", pattern):
            return float(p.get("input", 0)), float(p.get("output", 0))
    d = pricing.get("default") or {"input": default[0], "output": default[1]}
    return float(d.get("input", 0)), float(d.get("output", 0))


def format_number(value: int | float) -> str:
    value = int(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,}"


class RequestTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple] = []

    def refresh(self, rows: list[tuple]):
        if rows == self._rows:  # avoids reset churn (scroll/selection jumps) when idle
            return
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return len(REQUEST_COLUMNS)

    def headerData(self, section, orientation, role):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return REQUEST_COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        row = self._rows[index.row()]
        ts, ip, key_id, model, upstream, latency, ptok, ctok, cache, status = row
        if role == Qt.ItemDataRole.DisplayRole:
            speed = f"{ctok * 1000 / latency:.1f} t/s" if ctok > 0 and latency > 0 else "—"
            values = (datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S"), ip,
                      f"#{key_id}" if key_id else "—", model or "—", upstream or "—",
                      f"{latency:.0f} ms", f"{ptok:,}", f"{ctok:,}", speed, f"{cache:,}", status)
            return values[index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (2, 5, 6, 7, 8, 9, 10):
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 10:
            return {2: QColor("#22c55e"), 4: QColor("#f59e0b"),
                    5: QColor("#ef4444")}.get(status // 100) if status else None
        return None


class UsageTableModel(QAbstractTableModel):
    def __init__(self, pricing: dict | None = None, parent=None):
        super().__init__(parent)
        self._pricing = pricing or {}
        self._mode = "electricity"          # "electricity" | "api"
        self._raw_rows: list[tuple] = []
        self._raw_model_usage: list[tuple] = []
        self._rows: list[tuple] = []

    def _price_map(self) -> dict:
        p = self._pricing
        if isinstance(p, dict) and isinstance(p.get(self._mode), dict):
            return p[self._mode]
        return p  # legacy flat pricing

    def set_mode(self, mode: str):
        if mode == self._mode:
            return
        self._mode = mode
        self._rebuild()

    def _rebuild(self):
        agg: dict[str, dict] = {}
        price_map = self._price_map()
        for key_id, model, tin, tout in self._raw_model_usage:
            pin, pout = model_price(price_map, model)
            a = agg.setdefault(key_id, {"cost": 0.0, "prices": set()})
            a["cost"] += (tin * pin + tout * pout) / 1_000_000
            a["prices"].add((pin, pout))
        enriched = []
        for row in self._raw_rows:
            a = agg.get(row[0])
            if a:
                cost = a["cost"]
                if len(a["prices"]) == 1:
                    pin, pout = next(iter(a["prices"]))
                    label = f"¥{pin:g} / ¥{pout:g}"
                else:
                    label = "混合"
            else:
                cost, label = 0.0, "—"
            enriched.append(row + (label, f"¥{cost:,.4f}"))
        if enriched == self._rows:
            return
        self.beginResetModel()
        self._rows = enriched
        self.endResetModel()

    def refresh(self, rows: list[tuple], model_usage: list[tuple] | None = None):
        self._raw_rows = rows
        if model_usage is not None:
            self._raw_model_usage = model_usage
        self._rebuild()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return len(USAGE_COLUMNS)

    def headerData(self, section, orientation, role):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return USAGE_COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (row[0], row[1], row[2], f"{row[3]:,}", format_number(row[4]),
                      format_number(row[5]), format_number(row[6]), row[7], row[8])
            return values[index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (0, 3, 4, 5, 6, 7, 8):
            return Qt.AlignmentFlag.AlignCenter
        return None


class MetricCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")
        self.value = QLabel("—")
        self.value.setObjectName("metricValue")
        layout.addWidget(title_label)
        layout.addWidget(self.value)


class StatsBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: dict[str, MetricCard] = {}
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        specs = (("req_per_min", "请求 / 分钟"), ("avg_latency_ms", "平均延迟"),
                 ("tokens_in_24h", "24h 输入"), ("tokens_out_24h", "24h 输出"),
                 ("cache_hit_24h", "缓存命中"), ("errors_24h", "24h 错误"),
                 ("total_requests", "累计请求"))
        for index, (key, title) in enumerate(specs):
            card = MetricCard(title)
            layout.addWidget(card, index // 4, index % 4)
            self._cards[key] = card

    def update_stats(self, stats: dict):
        values = {
            "req_per_min": str(stats.get("req_per_min", 0)),
            "avg_latency_ms": f"{stats.get('avg_latency_ms', 0):,.0f} ms",
            "tokens_in_24h": format_number(stats.get("tokens_in_24h", 0)),
            "tokens_out_24h": format_number(stats.get("tokens_out_24h", 0)),
            "cache_hit_24h": f"{stats.get('cache_hit_24h', 0)}%",
            "errors_24h": str(stats.get("errors_24h", 0)),
            "total_requests": f"{stats.get('total_requests', 0):,}",
        }
        for key, value in values.items():
            self._cards[key].value.setText(value)


class ServiceCard(QFrame):
    action_requested = pyqtSignal(str, str)

    def __init__(self, service_name: str, title: str, description: str, parent=None):
        super().__init__(parent)
        self.service_name = service_name
        self.setObjectName("serviceCard")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(18, 16, 18, 16)
        self.root.setSpacing(10)

        heading = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("serviceTitle")
        self.status = QLabel("检测中")
        self.status.setObjectName("statusUnknown")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.addWidget(title_label)
        heading.addStretch(1)
        heading.addWidget(self.status)
        self.root.addLayout(heading)

        detail = QLabel(description)
        detail.setObjectName("serviceDescription")
        detail.setWordWrap(True)
        self.root.addWidget(detail)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("启动")
        self.stop_button = QPushButton("停止")
        self.restart_button = QPushButton("重启")
        self.start_button.setObjectName("primaryButton")
        self.stop_button.setObjectName("dangerButton")
        for button, action in ((self.start_button, "start"), (self.stop_button, "stop"),
                               (self.restart_button, "restart")):
            button.clicked.connect(lambda _checked=False, a=action: self.action_requested.emit(self.service_name, a))
            buttons.addWidget(button)
        self._buttons_index = self.root.count()
        self.root.addLayout(buttons)

    def add_extra(self, widget):
        """Insert an extra row above the button row (e.g. the model switcher)."""
        self._extra = widget
        self.root.insertWidget(self._buttons_index, widget)

    def set_status(self, state: str):
        running = state == "running"
        self.status.setText({"running": "运行中", "stopped": "已停止",
                             "not installed": "未安装"}.get(state, state))
        self.status.setObjectName("statusRunning" if running else "statusStopped")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.start_button.setEnabled(not running and state != "not installed")
        self.stop_button.setEnabled(running)
        self.restart_button.setEnabled(running)

    def set_busy(self):
        self.status.setText("处理中…")
        self.status.setObjectName("statusUnknown")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        for button in (self.start_button, self.stop_button, self.restart_button):
            button.setEnabled(False)
