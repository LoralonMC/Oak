"""Tests for metrics persistence.

Counters survive restarts by round-tripping through a JSON file. A corrupt or
hand-edited file must never stop the bot from starting.
"""

import json

from oak.metrics import Metrics


class TestCounters:
    def test_inc_creates_and_increments(self):
        m = Metrics()
        m.inc(m.commands, "ping")
        m.inc(m.commands, "ping", 4)
        assert m.commands["ping"] == 5

    def test_summary_covers_all_counters(self):
        m = Metrics()
        assert set(m.summary()) == {"commands", "events", "db_writes", "db_reads", "errors"}

    def test_summary_is_a_copy(self):
        m = Metrics()
        m.inc(m.commands, "ping")
        m.summary()["commands"]["ping"] = 999
        assert m.commands["ping"] == 1

    def test_reset_clears_and_restamps(self):
        m = Metrics()
        m.inc(m.errors, "boom")
        before = m.since
        m.reset()
        assert m.errors == {}
        assert m.since >= before


class TestPersistence:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "metrics.json"
        m = Metrics(path=path)
        m.inc(m.commands, "ping", 3)
        m.inc(m.errors, "app_cmd:foo")
        m.save()

        restored = Metrics(path=path)
        restored.load()
        assert restored.commands["ping"] == 3
        assert restored.errors["app_cmd:foo"] == 1
        assert restored.since == m.since

    def test_load_with_no_file_is_a_noop(self, tmp_path):
        m = Metrics(path=tmp_path / "absent.json")
        m.load()
        assert m.summary() == {k: {} for k in m.summary()}

    def test_load_ignores_corrupt_file(self, tmp_path):
        path = tmp_path / "metrics.json"
        path.write_text("{not json", encoding="utf-8")
        m = Metrics(path=path)
        m.load()  # must not raise
        assert m.commands == {}

    def test_load_ignores_wrong_shape(self, tmp_path):
        path = tmp_path / "metrics.json"
        path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        m = Metrics(path=path)
        m.load()
        assert m.commands == {}

    def test_load_skips_non_numeric_values(self, tmp_path):
        path = tmp_path / "metrics.json"
        path.write_text(json.dumps({"commands": {"ping": "lots", "pong": 2}}), encoding="utf-8")
        m = Metrics(path=path)
        m.load()
        assert m.commands == {"pong": 2}

    def test_save_creates_parent_directory(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "metrics.json"
        m = Metrics(path=path)
        m.inc(m.commands, "ping")
        m.save()
        assert path.exists()

    def test_save_without_path_is_a_noop(self):
        Metrics().save()  # must not raise

    def test_save_leaves_no_temp_files(self, tmp_path):
        path = tmp_path / "metrics.json"
        m = Metrics(path=path)
        m.inc(m.commands, "ping")
        m.save()
        m.save()
        assert [p.name for p in tmp_path.iterdir()] == ["metrics.json"]

    def test_accumulates_across_restarts(self, tmp_path):
        path = tmp_path / "metrics.json"
        first = Metrics(path=path)
        first.inc(first.commands, "ping", 2)
        first.save()

        second = Metrics(path=path)
        second.load()
        second.inc(second.commands, "ping", 3)
        second.save()

        third = Metrics(path=path)
        third.load()
        assert third.commands["ping"] == 5
