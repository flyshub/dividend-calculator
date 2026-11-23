"""scripts/sync_seed_db.py 的校验与安装逻辑（无网络）。"""

import sqlite3
from pathlib import Path

import pytest

from scripts import sync_seed_db as m


def _make_db(path: Path, *, universe: int = 1200, skip: tuple = (), coverage: int = 1100):
    con = sqlite3.connect(path)
    if "stock_list" not in skip:
        con.execute("CREATE TABLE stock_list (code TEXT PRIMARY KEY, name TEXT)")
        con.executemany("INSERT INTO stock_list VALUES (?,?)",
                        [(f"{i:06d}", f"股{i}") for i in range(universe)])
    for t in ("quote_snapshot", "dividend_snapshot", "finance_snapshot", "sustainability_snapshot"):
        if t in skip:
            continue
        con.execute(f"CREATE TABLE {t} (code TEXT PRIMARY KEY, updated_at TEXT)")
        rows = coverage if t != "sustainability_snapshot" else coverage // 5
        con.executemany(
            f"INSERT INTO {t} VALUES (?,?)",
            [(f"{i:06d}", "2026-11-20") for i in range(rows)])
    con.commit()
    con.close()
    return path


class _FakeResp:
    def __init__(self, payload=None):
        self._payload = payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_asset_url_picks_named_asset():
    payload = {"assets": [
        {"name": "screener_20261120.db", "browser_download_url": "https://x/old.db"},
        {"name": "screener.db", "browser_download_url": "https://x/new.db"},
    ]}
    assert m.asset_url(payload) == "https://x/new.db"


def test_asset_url_raises_when_absent():
    with pytest.raises(KeyError):
        m.asset_url({"assets": []})


def test_validate_ok(tmp_path):
    db = _make_db(tmp_path / "seed.db")
    stats = m.validate_seed(db)
    assert stats.ok
    assert stats.universe == 1200
    assert stats.counts["finance_snapshot"] == 1100
    assert stats.latest_updated["dividend_snapshot"] == "2026-11-20"


def test_validate_missing_table_not_ok(tmp_path):
    db = _make_db(tmp_path / "seed.db", skip=("finance_snapshot",))
    stats = m.validate_seed(db)
    assert not stats.ok
    assert "finance_snapshot" in m.format_report(stats)


def test_validate_small_universe_not_ok(tmp_path):
    db = _make_db(tmp_path / "seed.db", universe=50)
    stats = m.validate_seed(db)
    assert not stats.ok
    assert "< 1000" in m.format_report(stats)


def test_validate_empty_dividend_table_not_ok(tmp_path):
    # 只剩行情的库（2026-11 事故形状）：表齐但分红 0 行，不得通过
    db = _make_db(tmp_path / "seed.db", coverage=0)
    stats = m.validate_seed(db)
    assert not stats.ok


def test_validate_truncated_file_rejected(tmp_path):
    # 尾部被截断：sqlite 仍能读头部与行数，必须靠 quick_check 拦住
    db = _make_db(tmp_path / "seed.db", universe=5000, coverage=5000)
    data = db.read_bytes()
    db.write_bytes(data[:-200])
    with pytest.raises(sqlite3.DatabaseError):
        m.validate_seed(db)


def test_install_backs_up_and_replaces(tmp_path):
    dest = tmp_path / "screener.db"
    dest.write_bytes(b"old")
    new = _make_db(tmp_path / "new.db")
    payload = new.read_bytes()

    backup = m.install(new, dest)

    assert dest.read_bytes() == payload
    assert backup is not None and backup.read_bytes() == b"old"


def test_install_rolls_back_when_replace_fails(tmp_path, monkeypatch):
    dest = tmp_path / "screener.db"
    dest.write_bytes(b"old")
    new = _make_db(tmp_path / "new.db")

    real_replace = m.os.replace

    def failing_replace(src, dst):
        if Path(dst) == dest:
            raise OSError(18, "cross-device link")
        return real_replace(src, dst)

    monkeypatch.setattr(m.os, "replace", failing_replace)
    with pytest.raises(OSError):
        m.install(new, dest)

    assert dest.read_bytes() == b"old"      # 旧库已回滚
    assert dest.exists()


def test_install_prunes_old_backups(tmp_path):
    dest = tmp_path / "screener.db"
    dest.write_bytes(b"old")
    for i in range(9):
        (tmp_path / f"screener.db.bak-2026010{i + 1}-000000").write_bytes(b"x")

    m.install(_make_db(tmp_path / "new.db"), dest, keep_backups=3)

    assert len(list(tmp_path.glob("screener.db.bak-*"))) == 3


def test_install_no_backup(tmp_path):
    dest = tmp_path / "screener.db"
    dest.write_bytes(b"old")
    new = _make_db(tmp_path / "new.db")

    backup = m.install(new, dest, backup=False)

    assert backup is None
    assert [p.name for p in tmp_path.glob("screener.db.bak-*")] == []


def test_main_dry_run_keeps_existing(tmp_path, monkeypatch, capsys):
    dest = tmp_path / "screener.db"
    dest.write_bytes(b"old")
    monkeypatch.setattr(m.requests, "get", lambda *a, **k: _FakeResp(
        {"assets": [{"name": "screener.db", "browser_download_url": "https://x/seed.db"}]}))
    monkeypatch.setattr(m, "download", lambda url, path, timeout=180: _make_db(path))

    assert m.main(["--dest", str(dest), "--dry-run"]) == 0
    assert dest.read_bytes() == b"old"
    assert "--dry-run" in capsys.readouterr().out


def test_main_aborts_on_invalid_seed(tmp_path, monkeypatch):
    dest = tmp_path / "screener.db"
    dest.write_bytes(b"old")
    monkeypatch.setattr(m.requests, "get", lambda *a, **k: _FakeResp(
        {"assets": [{"name": "screener.db", "browser_download_url": "https://x/seed.db"}]}))
    monkeypatch.setattr(
        m, "download",
        lambda url, path, timeout=180: _make_db(path, skip=("stock_list",)))

    assert m.main(["--dest", str(dest)]) == 1
    assert dest.read_bytes() == b"old"


def test_main_success_installs_and_backs_up(tmp_path, monkeypatch):
    dest = tmp_path / "screener.db"
    dest.write_bytes(b"old")
    monkeypatch.setattr(m.requests, "get", lambda *a, **k: _FakeResp(
        {"assets": [{"name": "screener.db", "browser_download_url": "https://x/seed.db"}]}))
    monkeypatch.setattr(m, "download", lambda url, path, timeout=180: _make_db(path))

    assert m.main(["--dest", str(dest)]) == 0
    assert m.validate_seed(dest).ok
    assert [p.read_bytes() for p in tmp_path.glob("screener.db.bak-*")] == [b"old"]


def test_main_truncated_download_keeps_existing(tmp_path, monkeypatch):
    dest = tmp_path / "screener.db"
    dest.write_bytes(b"old")
    monkeypatch.setattr(m.requests, "get", lambda *a, **k: _FakeResp(
        {"assets": [{"name": "screener.db", "browser_download_url": "https://x/seed.db"}]}))

    def make_truncated(url, path, timeout=180):
        _make_db(path, universe=5000, coverage=5000)
        path.write_bytes(path.read_bytes()[:-200])

    monkeypatch.setattr(m, "download", make_truncated)

    assert m.main(["--dest", str(dest)]) == 1
    assert dest.read_bytes() == b"old"


def test_main_network_error_exits_1(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("dns")

    monkeypatch.setattr(m.requests, "get", boom)
    assert m.main(["--dest", str(tmp_path / "screener.db")]) == 1
