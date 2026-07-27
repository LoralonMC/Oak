"""Tests for the custom_id namespacing round-trip.

Persistent views depend on these ids surviving a restart, so a build/parse
mismatch silently breaks every button in the guild.
"""

import pytest

from oak.constants import CUSTOM_ID_MAX_LENGTH
from oak.errors import OakError
from oak.interactions import BranchInteractionHandle, InteractionRouter


@pytest.fixture
def router():
    return InteractionRouter()


@pytest.fixture
def handle(router):
    return BranchInteractionHandle(router, "tickets")


class TestRoundTrip:
    def test_action_only(self, handle):
        cid = handle.custom_id("close")
        parsed = handle.parse(cid)
        assert (parsed.branch, parsed.action, parsed.value) == ("tickets", "close", "")

    def test_action_with_value(self, handle):
        cid = handle.custom_id("snooze", "3600")
        parsed = handle.parse(cid)
        assert (parsed.branch, parsed.action, parsed.value) == ("tickets", "snooze", "3600")

    @pytest.mark.parametrize("value", ["abc", "A1", "a.b", "a_b", "a-b", "9"])
    def test_allowed_value_characters(self, handle, value):
        assert handle.parse(handle.custom_id("act", value)).value == value


class TestBuildValidation:
    def test_colon_in_action_rejected(self, handle):
        with pytest.raises(OakError):
            handle.custom_id("a:b")

    @pytest.mark.parametrize("value", ["a:b", "a b", "a/b", "a#b"])
    def test_invalid_value_rejected(self, handle, value):
        with pytest.raises(OakError):
            handle.custom_id("act", value)

    def test_over_length_rejected(self, handle):
        with pytest.raises(OakError):
            handle.custom_id("act", "x" * CUSTOM_ID_MAX_LENGTH)

    def test_at_limit_is_allowed(self, handle):
        prefix_len = len("oak:tickets:act:")
        cid = handle.custom_id("act", "x" * (CUSTOM_ID_MAX_LENGTH - prefix_len))
        assert len(cid) == CUSTOM_ID_MAX_LENGTH


class TestParsing:
    @pytest.mark.parametrize(
        "cid",
        [
            "notoak:tickets:act",
            "oak:tickets",
            "oak:tickets:act:val:extra",
            "oak::act",
            "oak:tickets:",
            "oak:tickets:act:has space",
        ],
    )
    def test_malformed_ids_return_none(self, router, cid):
        assert router.parse(cid) is None

    def test_handle_ignores_other_branches(self, handle, router):
        other = BranchInteractionHandle(router, "application")
        cid = other.custom_id("apply")
        # The router sees it, but the tickets handle must not claim it.
        assert router.parse(cid) is not None
        assert handle.parse(cid) is None
