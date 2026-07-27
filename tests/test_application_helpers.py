"""Tests for the application branch helpers."""

import pytest

from branches.application.helpers import (
    QUESTIONS_PER_PAGE,
    check_application_answer_quality,
    get_application_questions,
    get_embed_colors,
    is_staff,
    paginate_application_embed,
)
from oak.constants import EMBED_MAX_FIELDS


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


class TestIsStaff:
    def test_administrator_allowed(self):
        assert is_staff(FakeMember(administrator=True), [])

    def test_reviewer_role_allowed(self):
        assert is_staff(FakeMember([7]), [7])

    def test_unrelated_role_denied(self):
        assert not is_staff(FakeMember([1]), [7])

    def test_no_roles_denied(self):
        assert not is_staff(FakeMember(), [7])

    def test_object_without_roles_denied(self):
        assert not is_staff(object(), [7])


class TestAnswerQuality:
    def test_normal_answer_accepted(self):
        ok, msg = check_application_answer_quality("Why?", "Because I enjoy helping people.")
        assert ok and msg == ""

    @pytest.mark.parametrize("answer", ["", "   "])
    def test_empty_rejected(self, answer):
        ok, msg = check_application_answer_quality("Why?", answer)
        assert not ok and msg

    @pytest.mark.parametrize("answer", ["aaaa", ".....", "aaa", "  bbbb  "])
    def test_repeated_character_spam_rejected(self, answer):
        ok, _ = check_application_answer_quality("Why?", answer)
        assert not ok

    @pytest.mark.parametrize("answer", ["x", "no", "24"])
    def test_very_short_answers_are_allowed(self, answer):
        # The spam check only applies from 3 characters up, so a terse but
        # genuine answer ("no" to the punishment-history question) passes.
        # Judging quality is left to reviewers by design.
        ok, _ = check_application_answer_quality("Been banned?", answer)
        assert ok

    def test_short_but_real_answer_accepted(self):
        # Two distinct characters is enough to clear the spam check; staff
        # judge quality themselves.
        ok, _ = check_application_answer_quality("Age?", "24")
        assert ok


class TestGetApplicationQuestions:
    def test_returns_configured_questions(self):
        cfg = {"settings": {"questions": [{"label": "Q1"}]}}
        assert get_application_questions(cfg) == [{"label": "Q1"}]

    def test_falls_back_to_defaults_when_absent(self):
        assert len(get_application_questions({})) > 0

    def test_falls_back_when_empty_list(self):
        assert len(get_application_questions({"settings": {"questions": []}})) > 0


class TestGetEmbedColors:
    def test_defaults_present(self):
        colors = get_embed_colors({})
        assert set(colors) == {"info", "success", "warning", "error"}

    def test_config_overrides_default(self):
        colors = get_embed_colors({"settings": {"ui": {"embed_colors": {"info": 123}}}})
        assert colors["info"] == 123
        assert colors["error"] == 0xED4245


class TestPaginateApplicationEmbed:
    QUESTIONS = [{"label": f"Question {i}"} for i in range(10)]

    def test_single_page_for_short_answers(self):
        embeds = paginate_application_embed(None, ["short"] * 3, self.QUESTIONS[:3])
        assert len(embeds) == 1
        assert len(embeds[0].fields) == 3

    def test_handles_missing_applicant(self):
        embeds = paginate_application_embed(None, ["a"], self.QUESTIONS[:1])
        assert "Unknown Applicant" in embeds[0].title

    def test_no_answers_produces_no_embeds(self):
        assert paginate_application_embed(None, [], self.QUESTIONS) == []

    def test_extra_answers_are_truncated_to_questions(self):
        embeds = paginate_application_embed(None, ["a"] * 20, self.QUESTIONS[:2])
        assert sum(len(e.fields) for e in embeds) == 2

    def test_extra_questions_are_skipped(self):
        embeds = paginate_application_embed(None, ["a"] * 2, self.QUESTIONS)
        assert sum(len(e.fields) for e in embeds) == 2

    def test_long_answers_split_across_pages(self):
        answers = ["x" * 1000 for _ in range(10)]
        embeds = paginate_application_embed(None, answers, self.QUESTIONS)
        assert len(embeds) > 1
        # Every answer still appears exactly once.
        assert sum(len(e.fields) for e in embeds) == 10

    def test_field_values_within_discord_limit(self):
        answers = ["x" * 5000 for _ in range(3)]
        embeds = paginate_application_embed(None, answers, self.QUESTIONS[:3])
        for embed in embeds:
            for field in embed.fields:
                assert len(field.value) <= 1024

    def test_never_exceeds_field_count_limit(self):
        questions = [{"label": f"Q{i}"} for i in range(60)]
        embeds = paginate_application_embed(None, ["a"] * 60, questions)
        assert all(len(e.fields) <= EMBED_MAX_FIELDS for e in embeds)

    def test_pagination_terminates_on_pathological_input(self):
        # A single field far larger than the whole-embed budget must still
        # make forward progress rather than loop.
        embeds = paginate_application_embed(None, ["x" * 60000] * 3, self.QUESTIONS[:3])
        assert len(embeds) >= 1


def test_questions_per_page_matches_discord_modal_limit():
    # Discord allows at most 5 components in a modal. The modal split and the
    # post-restart resume step both derive from this constant, so a change
    # here silently re-asks or skips a page.
    assert QUESTIONS_PER_PAGE == 5
