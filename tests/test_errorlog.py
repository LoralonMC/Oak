"""Tests for the Discord error-log forwarding handler.

This handler sits in the logging path, so a bug here can break every log call
in the bot, and a feedback loop here can spam a channel indefinitely.
"""

import logging

import pytest

from oak.errorlog import ErrorReporter, _BufferingErrorHandler


@pytest.fixture
def handler():
    return _BufferingErrorHandler(level=logging.ERROR)


@pytest.fixture
def attached(handler, request):
    """A logger wired to the handler, isolated from the root logger."""
    log = logging.getLogger(f"oak.branch.test_{request.node.name}")
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.DEBUG)
    return log


class TestBuffering:
    def test_error_captured(self, handler, attached):
        attached.error("boom %s", 42)
        records = handler.drain()
        assert len(records) == 1
        assert "boom 42" in records[0][3]

    def test_below_level_filtered(self, handler, attached):
        attached.warning("ignored")
        attached.info("ignored")
        assert handler.drain() == []

    def test_critical_captured(self, handler, attached):
        attached.critical("very bad")
        assert len(handler.drain()) == 1

    def test_exception_traceback_included(self, handler, attached):
        try:
            raise ValueError("inner detail")
        except ValueError:
            attached.error("caught", exc_info=True)
        assert "ValueError: inner detail" in handler.drain()[0][3]

    def test_drain_clears_buffer(self, handler, attached):
        attached.error("one")
        assert len(handler.drain()) == 1
        assert handler.drain() == []

    def test_buffer_bounded_and_overflow_counted(self):
        handler = _BufferingErrorHandler(level=logging.ERROR, capacity=5)
        log = logging.getLogger("oak.branch.overflow")
        log.handlers = [handler]
        log.propagate = False
        for i in range(20):
            log.error("msg %d", i)
        assert len(handler.drain()) == 5
        assert handler.dropped == 15


class TestFeedbackLoopGuard:
    """Reporting a Discord failure over Discord would log an error, which
    re-queues and fails again forever."""

    @pytest.mark.parametrize("name", ["discord.client", "discord.http", "oak.errorlog"])
    def test_ignored_loggers_dropped(self, handler, name):
        log = logging.getLogger(name)
        log.handlers = [handler]
        log.propagate = False
        log.error("would loop")
        assert handler.drain() == []


class TestNeverRaises:
    """emit() is called from inside logging, so it must swallow everything.

    These build the LogRecord directly rather than going through a logger:
    pytest's own capture handler deliberately re-raises formatting errors to
    fail tests, which would mask whether *our* handler behaved.
    """

    @staticmethod
    def _record(msg, args):
        return logging.LogRecord(
            name="oak.branch.x",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=args,
            exc_info=None,
        )

    def test_bad_argument_repr_swallowed(self, handler):
        class Exploding:
            def __str__(self):
                raise RuntimeError("nope")

            def __repr__(self):
                raise RuntimeError("nope")

        handler.emit(self._record("bad arg: %s", (Exploding(),)))  # must not raise
        # Falls back to the raw template rather than dropping the report.
        assert len(handler.drain()) == 1

    def test_broken_format_string_swallowed(self, handler):
        handler.emit(self._record("missing arg %s %s", ("only-one",)))  # must not raise
        assert len(handler.drain()) == 1

    def test_unformattable_msg_object_swallowed(self, handler):
        class Exploding:
            def __str__(self):
                raise RuntimeError("nope")

        handler.emit(self._record(Exploding(), None))  # must not raise
        # Still reports a placeholder rather than dropping the error entirely.
        records = handler.drain()
        assert len(records) == 1
        assert "unformattable" in records[0][3]


class TestDedupeSignature:
    def test_first_line_only_so_varying_tail_collapses(self):
        sig = ErrorReporter._signature
        assert sig("a", "Failed to DM user 1\nstack A") == sig("a", "Failed to DM user 1\nstack B")

    def test_distinct_messages_kept_apart(self):
        sig = ErrorReporter._signature
        assert sig("a", "one") != sig("a", "two")

    def test_same_message_from_different_loggers_kept_apart(self):
        sig = ErrorReporter._signature
        assert sig("branch.a", "same") != sig("branch.b", "same")

    def test_long_messages_truncated_consistently(self):
        sig = ErrorReporter._signature
        assert sig("a", "x" * 500) == sig("a", "x" * 600)
