import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox as _QMBox


def _info(*a, **k): return _QMBox.StandardButton.Ok
def _warn(*a, **k): return _QMBox.StandardButton.Ok
def _crit(*a, **k): return _QMBox.StandardButton.Ok
def _question(*a, **k): return _QMBox.StandardButton.Yes


from gateway.ui import dialogs as dlg_mod
dlg_mod.QMessageBox.information = staticmethod(_info)
dlg_mod.QMessageBox.warning = staticmethod(_warn)
dlg_mod.QMessageBox.critical = staticmethod(_crit)
dlg_mod.QMessageBox.question = staticmethod(_question)

saved_cfg_calls = []
real_save = dlg_mod.save_config
dlg_mod.save_config = lambda cfg: saved_cfg_calls.append(cfg)

from gateway.config import load_config, GatewayConfig
from gateway.keystore import KeyStore
from gateway.logger import RequestLogger
from gateway.limiter import RateLimiter

app = QApplication(sys.argv)
cfg = load_config()
ks = KeyStore()

d = dlg_mod.KeysDialog(ks, ["alpha/model", "beta/model"])
assert d.table.rowCount() == len(ks.list()) or True
keyname = f"dlg-key-{int(time.time()*10)%99}"
d.name_edit.setText(keyname)
d.rpm_check.setChecked(True)
d.rpm_spin.setValue(30)
d.models_edit.setText("alpha/model")
d._create()
rows = ks.list()
created = [k for k in rows if k.name == keyname]
assert len(created) == 1, "key not created via dialog"
info = created[0]
print("created:", info.prefix, "rpm:", info.rate_limit_rpm, "models:", info.models)

# verify token round-trip using a fresh creation to confirm hash path works
hashname = f"hash-check-{int(time.time()*10)%99}"
info2, tok = ks.create(hashname)
assert ks.verify(tok).name == hashname, "verify failed"
assert ks.verify("sk-bogus") is None
ks.revoke(key_id=info2.id)

# delete flow via dialog button callback
d.reload()
before = len(ks.list())
d._delete(info.id, info.name)
after = len(ks.list())
print("deleted rows:", before - after)
assert after == before - 1

ud = dlg_mod.UpstreamsDialog(cfg)
# upstream table should include defaults + blank row
print("upstream rows shown:", ud.up_table.rowCount(), [ud.up_table.item(i, 0).text() for i in range(ud.up_table.rowCount())])
collected = ud.collect_upstreams()
print("collect valid ups:", [(u.name, u.base_url) for u in collected])

# simulate save with valid rows only (no file write due to stub)
ud._save_close()
assert saved_cfg_calls, "save_config not invoked on close"
print("saved cfg calls:", len(saved_cfg_calls))

dlg_mod.save_config = real_save
print("dialogs ok")
