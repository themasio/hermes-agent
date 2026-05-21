"""Room-mention pairing for unlisted senders (hive access-boundary contract).

Today an unlisted sender that @mentions the bot in a room is silently dropped
(run.py: the unauthorized branch only offers pairing when chat_type == "dm").
This adds an opt-in, per-platform `*_UNAUTHORIZED_ROOM_MENTION_BEHAVIOR` knob,
defaulting to "ignore" so existing behavior is preserved.

Spec: matrix-hive/docs/specs/2026-05-20-hermes-room-mention-pairing.md
"""

import asyncio
from types import SimpleNamespace

import pytest

from gateway.session import Platform, SessionSource


class _FakeAdapter:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text):
        self.sent.append((chat_id, text))


def _make_pairing_runner(code="ABC123", rate_limited=False):
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.config = None
    runner.adapters = {Platform.MATRIX: _FakeAdapter()}
    runner.pairing_store = SimpleNamespace(
        _is_rate_limited=lambda *_a, **_k: rate_limited,
        generate_code=lambda *_a, **_k: code,
        _record_rate_limit=lambda *_a, **_k: None,
    )
    return runner


def _room_source():
    return SessionSource(
        platform=Platform.MATRIX,
        chat_id="!room:srv",
        chat_type="group",
        user_id="@stranger:srv",
        user_name="Stranger",
    )


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for var in (
        "MATRIX_UNAUTHORIZED_ROOM_MENTION_BEHAVIOR",
        "TELEGRAM_UNAUTHORIZED_ROOM_MENTION_BEHAVIOR",
    ):
        monkeypatch.delenv(var, raising=False)


def _make_bare_runner():
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.config = None  # resolver must tolerate no config object
    return runner


def test_room_mention_behavior_defaults_ignore(monkeypatch):
    """No config / no env → "ignore" (preserve current silent behavior)."""
    runner = _make_bare_runner()
    assert runner._get_unauthorized_room_mention_behavior(Platform.MATRIX) == "ignore"


def test_room_mention_behavior_pair_when_env_set(monkeypatch):
    """MATRIX_UNAUTHORIZED_ROOM_MENTION_BEHAVIOR=pair opts in."""
    runner = _make_bare_runner()
    monkeypatch.setenv("MATRIX_UNAUTHORIZED_ROOM_MENTION_BEHAVIOR", "pair")
    assert runner._get_unauthorized_room_mention_behavior(Platform.MATRIX) == "pair"


def test_room_mention_behavior_invalid_falls_back_to_ignore(monkeypatch):
    """Garbage value normalizes to the safe default."""
    runner = _make_bare_runner()
    monkeypatch.setenv("MATRIX_UNAUTHORIZED_ROOM_MENTION_BEHAVIOR", "banana")
    assert runner._get_unauthorized_room_mention_behavior(Platform.MATRIX) == "ignore"


def test_room_mention_behavior_is_per_platform(monkeypatch):
    """A Matrix knob must not leak into another platform."""
    runner = _make_bare_runner()
    monkeypatch.setenv("MATRIX_UNAUTHORIZED_ROOM_MENTION_BEHAVIOR", "pair")
    assert runner._get_unauthorized_room_mention_behavior(Platform.TELEGRAM) == "ignore"


def test_offer_room_mention_pairing_sends_code():
    """The offer posts a pairing code to the room via the platform adapter."""
    runner = _make_pairing_runner(code="ABC123")
    asyncio.run(runner._offer_room_mention_pairing(_room_source()))
    sent = runner.adapters[Platform.MATRIX].sent
    assert len(sent) == 1
    chat_id, text = sent[0]
    assert chat_id == "!room:srv"
    assert "ABC123" in text


def test_offer_room_mention_pairing_silent_when_rate_limited():
    """Rate-limited → no message (prevents room spam)."""
    runner = _make_pairing_runner(rate_limited=True)
    asyncio.run(runner._offer_room_mention_pairing(_room_source()))
    assert runner.adapters[Platform.MATRIX].sent == []
