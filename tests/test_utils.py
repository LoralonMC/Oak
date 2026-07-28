"""Tests for oak.utils and oak.config pure helpers."""

import pytest
import yaml

from oak.config import (
    deep_merge,
    load_branch_config,
    resolve_config_path,
    write_branch_enabled,
)
from oak.utils import paginate, sanitize_text, truncate, truncate_for_embed_field


class TestSanitizeText:
    def test_strips_null_bytes(self):
        assert sanitize_text("a\x00b") == "ab"

    def test_trims_whitespace(self):
        assert sanitize_text("  hi  ") == "hi"

    def test_truncates_to_max_length(self):
        assert len(sanitize_text("x" * 500, max_length=100)) == 100

    @pytest.mark.parametrize("value", ["", None])
    def test_empty_input(self, value):
        assert sanitize_text(value) == ""

    def test_truncation_happens_before_strip(self):
        # Truncating mid-string can expose trailing whitespace, which is then
        # stripped. The result must never exceed max_length either way.
        assert sanitize_text("ab   cd", max_length=5) == "ab"


class TestTruncate:
    def test_short_text_untouched(self):
        assert truncate("hello", 10) == "hello"

    def test_appends_suffix_and_respects_limit(self):
        out = truncate("x" * 50, 10)
        assert len(out) == 10
        assert out.endswith("...")

    def test_suffix_longer_than_limit_falls_back_to_hard_cut(self):
        assert truncate("abcdef", 2, suffix="......") == "ab"

    def test_none_becomes_empty(self):
        assert truncate(None, 10) == ""


class TestTruncateForEmbedField:
    def test_default_limit_is_discord_field_max(self):
        assert len(truncate_for_embed_field("x" * 5000)) == 1024

    def test_exact_length_not_truncated(self):
        text = "y" * 1024
        assert truncate_for_embed_field(text) == text

    def test_empty_input(self):
        assert truncate_for_embed_field("") == ""


class TestPaginate:
    def test_short_text_single_page(self):
        assert paginate("hello", 100) == ["hello"]

    def test_empty_text_no_pages(self):
        assert paginate("", 100) == []

    def test_prefers_line_boundaries(self):
        text = "aaaa\nbbbb\ncccc\n"
        pages = paginate(text, 10)
        assert all(len(p) <= 10 for p in pages)
        # Nothing is lost or duplicated by the split.
        assert "".join(pages) == text
        # A page should not end mid-line when a newline was available.
        assert pages[0].endswith("\n")

    def test_hard_cut_when_no_newline_fits(self):
        text = "x" * 25
        pages = paginate(text, 10)
        assert [len(p) for p in pages] == [10, 10, 5]
        assert "".join(pages) == text

    def test_rejects_non_positive_page_size(self):
        with pytest.raises(ValueError):
            paginate("abc", 0)


class TestDeepMerge:
    def test_override_wins_on_scalars(self):
        assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested_dicts_merge_rather_than_replace(self):
        base = {"ui": {"color": 1, "size": 2}}
        out = deep_merge(base, {"ui": {"color": 9}})
        assert out == {"ui": {"color": 9, "size": 2}}

    def test_missing_keys_come_from_base(self):
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_lists_are_replaced_not_concatenated(self):
        assert deep_merge({"r": [1, 2]}, {"r": [3]}) == {"r": [3]}

    def test_result_does_not_alias_inputs(self):
        # A shared nested reference would let a later config edit mutate the
        # defaults for every other branch.
        base = {"nested": {"list": [1]}}
        override = {"other": {"list": [2]}}
        out = deep_merge(base, override)
        out["nested"]["list"].append(99)
        out["other"]["list"].append(99)
        assert base["nested"]["list"] == [1]
        assert override["other"]["list"] == [2]

    def test_inputs_are_not_mutated(self):
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}
        deep_merge(base, override)
        assert base == {"a": {"b": 1}}
        assert override == {"a": {"c": 2}}


class TestConfigResolution:
    """Live config.yml is gitignored; config.example.yml is committed. A fresh
    clone must still start, and enabling/disabling must not destroy settings."""

    def _write(self, path, data):
        path.write_text(yaml.safe_dump(data), encoding="utf-8")

    def test_prefers_live_config(self, tmp_path):
        self._write(tmp_path / "config.yml", {"settings": {"a": "live"}})
        self._write(tmp_path / "config.example.yml", {"settings": {"a": "example"}})
        assert resolve_config_path(tmp_path).name == "config.yml"

    def test_falls_back_to_example(self, tmp_path):
        self._write(tmp_path / "config.example.yml", {"settings": {"a": "example"}})
        assert resolve_config_path(tmp_path).name == "config.example.yml"

    def test_none_when_neither_exists(self, tmp_path):
        assert resolve_config_path(tmp_path) is None

    def test_load_uses_example_when_live_absent(self, tmp_path):
        self._write(tmp_path / "config.example.yml", {"settings": {"a": "example"}})
        assert load_branch_config(tmp_path, {}, "t")["settings"]["a"] == "example"

    def test_load_falls_back_to_defaults_with_no_files(self, tmp_path):
        assert load_branch_config(tmp_path, {"settings": {"a": 1}}, "t") == {"settings": {"a": 1}}

    def test_write_enabled_seeds_from_example(self, tmp_path):
        # Otherwise /disable writes a config.yml containing only enabled:false,
        # which then shadows the example and strips every other setting.
        self._write(tmp_path / "config.example.yml", {"enabled": True, "settings": {"a": "kept"}})
        write_branch_enabled(tmp_path, False)
        written = yaml.safe_load((tmp_path / "config.yml").read_text(encoding="utf-8"))
        assert written["enabled"] is False
        assert written["settings"]["a"] == "kept"

    def test_write_enabled_preserves_live_config(self, tmp_path):
        self._write(tmp_path / "config.yml", {"enabled": True, "settings": {"a": "live"}})
        write_branch_enabled(tmp_path, False)
        written = yaml.safe_load((tmp_path / "config.yml").read_text(encoding="utf-8"))
        assert written["enabled"] is False and written["settings"]["a"] == "live"

    def test_write_enabled_leaves_no_temp_files(self, tmp_path):
        self._write(tmp_path / "config.yml", {"settings": {}})
        write_branch_enabled(tmp_path, True)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yml"]
