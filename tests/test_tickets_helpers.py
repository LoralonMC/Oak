"""Tests for the tickets branch helpers."""

import copy

import pytest

from branches.tickets.helpers import (
    hash_config,
    member_can_manage_category,
    parse_time_string,
    sanitize_name,
    validate_config,
)


class FakeRole:
    def __init__(self, id):
        self.id = id


class FakePerms:
    def __init__(self, administrator=False):
        self.administrator = administrator


class FakeMember:
    def __init__(self, role_ids=(), administrator=False):
        self.roles = [FakeRole(r) for r in role_ids]
        self.guild_permissions = FakePerms(administrator)


class TestParseTimeString:
    @pytest.mark.parametrize(
        "value,expected",
        [("30m", 1800), ("2h", 7200), ("1d", 86400), ("60", 3600), ("1M", 60)],
    )
    def test_valid_values(self, value, expected):
        assert parse_time_string(value) == expected

    @pytest.mark.parametrize("value", ["", None, "abc", "5x", "-5m", "1.5h", "m", "5 m"])
    def test_invalid_values(self, value):
        assert parse_time_string(value) is None

    @pytest.mark.parametrize("value", ["0m", "0h", "0d", "0"])
    def test_zero_durations_rejected(self, value):
        assert parse_time_string(value) is None

    @pytest.mark.parametrize("value", ["31d", "721h", "43201m", "43201"])
    def test_over_thirty_days_rejected(self, value):
        assert parse_time_string(value) is None

    @pytest.mark.parametrize("value", ["30d", "720h", "43200m"])
    def test_exactly_thirty_days_allowed(self, value):
        assert parse_time_string(value) == 2592000

    def test_whitespace_tolerated(self):
        assert parse_time_string("  2h  ") == 7200


class TestSanitizeName:
    def test_spaces_become_hyphens(self):
        assert sanitize_name("some player") == "some-player"

    def test_lowercased(self):
        assert sanitize_name("LoudName") == "loudname"

    def test_special_characters_removed(self):
        assert sanitize_name("we!!ird@name") == "weirdname"

    def test_unicode_preserved(self):
        assert sanitize_name("Ütherìc") == "ütherìc"

    def test_consecutive_hyphens_collapsed(self):
        assert sanitize_name("a   b") == "a-b"

    def test_leading_trailing_hyphens_stripped(self):
        assert sanitize_name("  name  ") == "name"

    def test_length_capped_at_discord_limit(self):
        assert len(sanitize_name("x" * 200)) == 100

    def test_empty_falls_back_to_user_id(self):
        assert sanitize_name("!!!", user_id=42) == "user-42"

    def test_empty_without_user_id_falls_back_to_ticket(self):
        assert sanitize_name("!!!") == "ticket"


class TestMemberCanManageCategory:
    CONFIG = {
        "settings": {
            "staff_role_ids": [100],
            "categories": {
                "billing": {"staff_roles": [200]},
                "legacy": {"ping_roles": [300]},
                "nobody": {},
            },
        }
    }

    def test_administrator_always_allowed(self):
        assert member_can_manage_category(FakeMember(administrator=True), "nobody", self.CONFIG)

    def test_global_staff_role_allowed(self):
        assert member_can_manage_category(FakeMember([100]), "billing", self.CONFIG)

    def test_category_role_allowed(self):
        assert member_can_manage_category(FakeMember([200]), "billing", self.CONFIG)

    def test_category_role_does_not_leak_across_categories(self):
        assert not member_can_manage_category(FakeMember([200]), "nobody", self.CONFIG)

    def test_ping_roles_backwards_compatibility(self):
        assert member_can_manage_category(FakeMember([300]), "legacy", self.CONFIG)

    def test_unrelated_role_denied(self):
        assert not member_can_manage_category(FakeMember([999]), "billing", self.CONFIG)

    def test_unknown_category_denied(self):
        assert not member_can_manage_category(FakeMember([200]), "does_not_exist", self.CONFIG)


class TestHashConfig:
    """The panel is deleted and reposted whenever this hash changes, so it must
    react to rendered fields only."""

    BASE = {
        "settings": {
            "panel": {"title": "T", "description": "D", "color": 1, "categories_field_name": "C"},
            "categories": {
                "a": {"label": "A", "emoji": "x", "description": "d", "enabled": True},
                "b": {"label": "B", "emoji": "y", "description": "e", "enabled": False},
            },
            "transcript": {"web": {"base_url": "http://localhost:5454"}},
            "staff_role_ids": [1, 2],
            "log_channel_id": 5,
        }
    }

    def _mutated(self, fn):
        cfg = copy.deepcopy(self.BASE)
        fn(cfg)
        return hash_config(cfg)

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda c: c["settings"]["transcript"]["web"].update(base_url="https://x"),
            lambda c: c["settings"].update(staff_role_ids=[9]),
            lambda c: c["settings"].update(log_channel_id=99),
            lambda c: c["settings"]["categories"]["b"].update(label="renamed-while-disabled"),
            lambda c: c["settings"].update(sla={"first_response_hours": 6}),
        ],
    )
    def test_invisible_changes_do_not_churn_the_panel(self, mutate):
        assert self._mutated(mutate) == hash_config(self.BASE)

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda c: c["settings"]["panel"].update(title="NEW"),
            lambda c: c["settings"]["panel"].update(description="NEW"),
            lambda c: c["settings"]["panel"].update(color=2),
            lambda c: c["settings"]["panel"].update(categories_field_name="NEW"),
            lambda c: c["settings"]["categories"]["a"].update(label="RENAMED"),
            lambda c: c["settings"]["categories"]["a"].update(emoji="z"),
            lambda c: c["settings"]["categories"]["a"].update(description="new"),
            lambda c: c["settings"]["categories"]["b"].update(enabled=True),
            lambda c: c["settings"]["categories"].pop("a"),
        ],
    )
    def test_visible_changes_trigger_a_repost(self, mutate):
        assert self._mutated(mutate) != hash_config(self.BASE)

    def test_stable_across_calls(self):
        assert hash_config(self.BASE) == hash_config(copy.deepcopy(self.BASE))

    def test_key_order_does_not_matter(self):
        reordered = {
            "settings": {
                "categories": dict(reversed(list(self.BASE["settings"]["categories"].items()))),
                "panel": dict(reversed(list(self.BASE["settings"]["panel"].items()))),
            }
        }
        reordered["settings"]["transcript"] = self.BASE["settings"]["transcript"]
        reordered["settings"]["staff_role_ids"] = self.BASE["settings"]["staff_role_ids"]
        reordered["settings"]["log_channel_id"] = self.BASE["settings"]["log_channel_id"]
        assert hash_config(reordered) == hash_config(self.BASE)


class TestValidateConfig:
    def _config(self, **overrides):
        settings = {
            "ticket_panel_channel_id": 1,
            "log_channel_id": 2,
            "staff_role_ids": [3],
            "categories": {
                "support": {
                    "naming_pattern": "ticket-{number}",
                    "welcome_message": "hi",
                }
            },
        }
        settings.update(overrides)
        return {"settings": settings}

    def test_valid_config_passes(self):
        assert validate_config(self._config()) == (True, [])

    def test_missing_panel_channel_flagged(self):
        ok, errors = validate_config(self._config(ticket_panel_channel_id=0))
        assert not ok and any("ticket_panel_channel_id" in e for e in errors)

    def test_placeholder_staff_roles_flagged(self):
        ok, errors = validate_config(self._config(staff_role_ids=[0]))
        assert not ok and any("placeholder" in e for e in errors)

    def test_empty_staff_roles_flagged(self):
        ok, errors = validate_config(self._config(staff_role_ids=[]))
        assert not ok

    def test_no_categories_flagged(self):
        ok, errors = validate_config(self._config(categories={}))
        assert not ok and any("categories" in e for e in errors)

    def test_bad_naming_pattern_flagged(self):
        ok, errors = validate_config(
            self._config(categories={"s": {"naming_pattern": "static", "welcome_message": "hi"}})
        )
        assert not ok and any("naming_pattern" in e for e in errors)

    def test_disabled_category_not_validated(self):
        ok, _ = validate_config(
            self._config(categories={"s": {"enabled": False}, "t": {
                "naming_pattern": "t-{number}", "welcome_message": "hi"}})
        )
        assert ok
