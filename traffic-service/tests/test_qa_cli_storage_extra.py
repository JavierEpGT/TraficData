"""Additional independent QA regression scenarios for the CLI and local storage.

Run: python -m pytest tests/test_qa_cli_storage_extra.py -v
No actual external API, Group 1 server, or remote database is used.
"""
from __future__ import annotations

import csv
import sqlite3
from argparse import Namespace
from types import SimpleNamespace

import pytest

from app import cli
from app.storage import RawReading, Storage, iter_points_from_rows


def args(**overrides):
    values = dict(from_g1=False, csv="ignored.csv", loop=False, interval=0)
    values.update(overrides)
    return Namespace(**values)


def reading(identifier="a", *, status="SENT", index=0.4, timestamp="2026-11-14T10:00:00Z", created_at=None):
    return RawReading(
        reading_id=identifier, timestamp=timestamp, lat=14.6, lon=-90.5,
        status=status, traffic_congestion_index=index, created_at=created_at,
    )


@pytest.fixture
def local_storage(tmp_path):
    return Storage(db_path=str(tmp_path / "nested" / "readings.db"))


def test_storage_creates_parent_directory_and_empty_db(tmp_path):
    path = tmp_path / "deep" / "sub" / "readings.db"
    db = Storage(str(path))
    assert path.exists()
    assert db.fetch_all() == []


def test_storage_insert_and_fetch_one(local_storage):
    local_storage.insert_reading(reading())
    rows = local_storage.fetch_all()
    assert len(rows) == 1
    assert rows[0]["reading_id"] == "a"
    assert rows[0]["lat"] == 14.6
    assert rows[0]["created_at"]


def test_storage_preserves_explicit_created_at(local_storage):
    date = "2026-11-14T08:00:00Z"
    local_storage.insert_reading(reading(created_at=date))
    assert local_storage.fetch_all()[0]["created_at"] == date


def test_storage_duplicate_reading_id_fails(local_storage):
    local_storage.insert_reading(reading("same"))
    with pytest.raises(sqlite3.IntegrityError):
        local_storage.insert_reading(reading("same"))
    assert len(local_storage.fetch_all()) == 1


def test_storage_fetch_all_order_by_created_at(local_storage):
    local_storage.insert_reading(reading("later", created_at="2026-11-14T11:00:00Z"))
    local_storage.insert_reading(reading("earlier", created_at="2026-11-14T09:00:00Z"))
    assert [row["reading_id"] for row in local_storage.fetch_all()] == ["earlier", "later"]


def test_storage_valid_readings_filters_status_and_null_index(local_storage):
    local_storage.insert_reading(reading("sent", status="SENT"))
    local_storage.insert_reading(reading("dry", status="DRY_RUN", index=0.0))
    local_storage.insert_reading(reading("failed", status="FAILED"))
    local_storage.insert_reading(reading("null", index=None))
    assert sorted(row["reading_id"] for row in local_storage.fetch_valid_readings()) == ["dry", "sent"]


def test_storage_valid_readings_since_is_inclusive(local_storage):
    local_storage.insert_reading(reading("before", timestamp="2026-11-14T09:59:59Z"))
    local_storage.insert_reading(reading("exact", timestamp="2026-11-14T10:00:00Z"))
    local_storage.insert_reading(reading("after", timestamp="2026-11-14T10:01:00Z"))
    found = local_storage.fetch_valid_readings(since="2026-11-14T10:00:00Z")
    assert [row["reading_id"] for row in found] == ["exact", "after"]


def test_storage_valid_readings_limit_and_tie_order(local_storage):
    for name in ("z", "b", "a"):
        local_storage.insert_reading(reading(name))
    assert [row["reading_id"] for row in local_storage.fetch_valid_readings(limit=2)] == ["a", "b"]


def test_storage_export_empty_csv_contains_header(local_storage, tmp_path):
    output = tmp_path / "export" / "empty.csv"
    assert local_storage.export_csv(str(output)) == 0
    with output.open(encoding="utf-8", newline="") as stream:
        items = list(csv.DictReader(stream))
    assert items == []
    assert "reading_id" in output.read_text(encoding="utf-8")


def test_storage_export_rows_csv(local_storage, tmp_path):
    local_storage.insert_reading(reading("one"))
    path = tmp_path / "results.csv"
    assert local_storage.export_csv(str(path)) == 1
    with path.open(encoding="utf-8", newline="") as handle:
        assert next(csv.DictReader(handle))["reading_id"] == "one"


def test_iter_points_casts_numeric_strings():
    assert iter_points_from_rows([{"lat": "14.5", "lon": "-90.5"}]) == [(14.5, -90.5)]


def test_iter_points_bad_coordinate_raises():
    with pytest.raises(ValueError):
        iter_points_from_rows([{"lat": "not-number", "lon": "-90.5"}])


def test_cli_load_csv_points(monkeypatch, settings):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    class FakeCsv:
        def __init__(self, path):
            assert path == "input.csv"
        def get_points(self):
            return [(14.5, -90.5)]
    monkeypatch.setattr(cli, "CsvSource", FakeCsv)
    assert cli._load_points(args(csv="input.csv")) == [(14.5, -90.5)]


def test_cli_load_g1_missing_credentials(monkeypatch, settings):
    settings.group1_api_url = None
    settings.group1_api_token = None
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    with pytest.raises(ValueError, match="GROUP1_API_URL"):
        cli._load_points(args(from_g1=True))


def test_cli_load_g1_success(monkeypatch, settings):
    settings.group1_api_url = "https://example.invalid/points"
    settings.group1_api_token = "fake-token"
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    class FakeG1:
        def __init__(self, url, token):
            assert url == settings.group1_api_url
            assert token == settings.group1_api_token
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get_points(self):
            return [(14.4, -90.4)]
    monkeypatch.setattr(cli, "Group1ApiSource", FakeG1)
    assert cli._load_points(args(from_g1=True)) == [(14.4, -90.4)]


@pytest.mark.parametrize("exception", [FileNotFoundError("missing"), ValueError("bad")])
def test_cli_run_handles_source_errors(monkeypatch, settings, exception):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    def broken(_args):
        raise exception
    monkeypatch.setattr(cli, "_load_points", broken)
    assert cli.cmd_run(args()) == 1


def test_cli_run_rejects_no_points(monkeypatch, settings):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "_load_points", lambda a: [])
    assert cli.cmd_run(args()) == 1


def test_cli_run_single_batch_calls_pipeline(monkeypatch, settings):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "_load_points", lambda a: [(14.1, -90.1), (14.2, -90.2)])
    processed = []
    monkeypatch.setattr(cli, "Storage", lambda *a: object())
    def fake_process(lat, lon, settings, storage):
        processed.append((lat, lon))
        return SimpleNamespace(status="DRY_RUN", error=None)
    monkeypatch.setattr(cli, "process_reading", fake_process)
    assert cli.cmd_run(args()) == 0
    assert processed == [(14.1, -90.1), (14.2, -90.2)]


def test_cli_run_loop_csv_does_not_reload_source(monkeypatch, settings):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    loads = []
    def load(a):
        loads.append(1)
        return [(14.1, -90.1)]
    monkeypatch.setattr(cli, "_load_points", load)
    monkeypatch.setattr(cli, "Storage", lambda *a: object())
    processed = []
    def fake_process(*a, **kw):
        processed.append(1)
        return SimpleNamespace(status="SENT", error=None)
    monkeypatch.setattr(cli, "process_reading", fake_process)
    def stop_on_sleep(interval):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli.time, "sleep", stop_on_sleep)
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_run(args(loop=True))
    assert len(loads) == 1
    assert len(processed) == 1


def test_cli_run_g1_refresh_error(monkeypatch, settings):
    settings.database_url = "postgresql://fake.invalid/db"
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    times = iter([[(14.1, -90.1)], ValueError("G1 down")])
    def fake_load(a):
        value = next(times)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(cli, "_load_points", fake_load)
    monkeypatch.setattr(cli, "Storage", lambda *a: object())
    monkeypatch.setattr(cli, "process_reading", lambda *a, **kw: SimpleNamespace(status="SENT", error=None))
    assert cli.cmd_run(args(from_g1=True, loop=True)) == 1


def test_cli_run_g1_refresh_empty(monkeypatch, settings):
    settings.database_url = "postgresql://fake.invalid/db"
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    batches = iter([[(14.1, -90.1)], []])
    monkeypatch.setattr(cli, "_load_points", lambda a: next(batches))
    monkeypatch.setattr(cli, "Storage", lambda *a: object())
    monkeypatch.setattr(cli, "process_reading", lambda *a, **kw: SimpleNamespace(status="SENT", error=None))
    assert cli.cmd_run(args(from_g1=True, loop=True)) == 1


def test_cli_run_g1_refresh_success_two_iterations(monkeypatch, settings):
    settings.database_url = "postgresql://fake.invalid/db"
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    batches = iter([[(14.1, -90.1)], [(14.2, -90.2)]])
    monkeypatch.setattr(cli, "_load_points", lambda a: next(batches))
    monkeypatch.setattr(cli, "Storage", lambda *a: object())
    processed = []
    def process(lat, lon, *_a, **_kw):
        processed.append((lat, lon))
        if len(processed) == 2:
            raise KeyboardInterrupt
        return SimpleNamespace(status="SENT", error=None)
    monkeypatch.setattr(cli, "process_reading", process)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_run(args(from_g1=True, loop=True))
    assert processed == [(14.1, -90.1), (14.2, -90.2)]


def test_cli_export_csv(monkeypatch, settings, tmp_path, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    output = tmp_path / "cli.csv"
    assert cli.cmd_export_csv(Namespace(out=str(output))) == 0
    assert output.exists()
    assert "Exported 0 rows" in capsys.readouterr().out


def test_cli_parser_requires_source_option():
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as err:
        parser.parse_args(["run"])
    assert err.value.code == 2


def test_cli_parser_rejects_two_sources():
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as err:
        parser.parse_args(["run", "--csv", "f.csv", "--from-g1"])
    assert err.value.code == 2


def test_cli_parser_run_options():
    parsed = cli.build_parser().parse_args(["run", "--csv", "p.csv", "--interval", "10"])
    assert parsed.func is cli.cmd_run
    assert parsed.interval == 10
    assert parsed.csv == "p.csv"


def test_cli_parser_export_requires_out():
    with pytest.raises(SystemExit) as err:
        cli.build_parser().parse_args(["export-csv"])
    assert err.value.code == 2


def test_cli_main_dispatches_export(monkeypatch, settings, tmp_path):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    out = tmp_path / "main.csv"
    assert cli.main(["export-csv", "--out", str(out)]) == 0
    assert out.exists()
