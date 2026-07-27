"""Tests for the status_channels format-string guard.

Channel name formats come from config, and ``str.format`` on attacker- or
typo-supplied templates can reach into object internals. The validator only
permits bare placeholders from a known set.
"""

import pytest

from branches.status_channels.branch import _validate_format


class TestAllowed:
    @pytest.mark.parametrize(
        "fmt,keys",
        [
            ("Total: {count}", {"count"}),
            ("Total: {count:,}", {"count"}),
            ("Online: {online}/{max}", {"online", "max"}),
            ("no placeholders at all", {"count"}),
            ("{count:>10}", {"count"}),
            ("{count!r}", {"count"}),
        ],
    )
    def test_valid_formats(self, fmt, keys):
        assert _validate_format(fmt, keys)


class TestRejected:
    @pytest.mark.parametrize(
        "fmt",
        [
            "{count.__class__}",
            "{count.__class__.__mro__}",
            "{count[0]}",
            "{0.__class__}",
        ],
    )
    def test_attribute_and_index_access_rejected(self, fmt):
        assert not _validate_format(fmt, {"count"})

    @pytest.mark.parametrize("fmt", ["{unknown}", "{online}", "{count} {other}"])
    def test_unknown_keys_rejected(self, fmt):
        assert not _validate_format(fmt, {"count"})


def test_defaults_in_branch_config_are_valid():
    """The shipped defaults must pass their own validator."""
    from branches.status_channels.branch import DEFAULT_CONFIG

    formats = DEFAULT_CONFIG["settings"]["formats"]
    assert _validate_format(formats["member_count"], {"count"})
    assert _validate_format(formats["player_count"], {"online", "max"})
