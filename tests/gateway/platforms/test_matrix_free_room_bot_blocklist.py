"""Free-room bot blocklist (anti-loop for cross-Hermes-bot traffic).

In a free room, messages from senders on ``MATRIX_FREE_ROOM_BOT_BLOCKLIST``
do NOT get the require_mention bypass — they still need an explicit
``@mention`` to engage the bot. Closes the cross-bot loop that the bridge
filter (``_is_system_or_bridge_sender``) doesn't catch for peer Hermes
profiles whose MXIDs don't follow the appservice ``@_…`` convention.

Spec: matrix-hive/docs/specs/2026-05-28-coordinator-ambient-watcher.md §3.2
Pattern context: matrix-hive-wiki/ideas/2026-05-28-stigmergic-consult-pattern.md
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig


def _make_adapter():
    """Create a MatrixAdapter with mocked config."""
    from plugins.platforms.matrix.adapter import MatrixAdapter

    config = PlatformConfig(
        enabled=True,
        token="syt_test_token",
        extra={
            "homeserver": "https://matrix.example.org",
            "user_id": "@hermes:example.org",
        },
    )
    adapter = MatrixAdapter(config)
    adapter._text_batch_delay_seconds = 0  # disable batching for tests
    adapter.handle_message = AsyncMock()
    adapter._startup_ts = time.time() - 10  # avoid startup grace filter
    return adapter


def _make_event(body, sender="@alice:example.org", room_id="!room1:example.org"):
    """Create a fake room message event in the shape mautrix delivers."""
    return SimpleNamespace(
        sender=sender,
        event_id="$evt_blocklist_test",
        room_id=room_id,
        timestamp=int(time.time() * 1000),
        content={"body": body, "msgtype": "m.text"},
    )


# ---------------------------------------------------------------------------
# Free room with bot blocklist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_free_room_human_no_mention_engages(monkeypatch):
    """Free room + non-blocklisted sender + no mention → engage (existing
    ambient bypass)."""
    monkeypatch.delenv("MATRIX_REQUIRE_MENTION", raising=False)
    monkeypatch.setenv("MATRIX_FREE_RESPONSE_ROOMS", "!room1:example.org")
    monkeypatch.setenv(
        "MATRIX_FREE_ROOM_BOT_BLOCKLIST",
        "@field:example.org,@coordinator:example.org",
    )
    monkeypatch.setenv("MATRIX_AUTO_THREAD", "false")

    adapter = _make_adapter()
    event = _make_event(
        "ambient drop without mention", sender="@alice:example.org"
    )

    await adapter._on_room_message(event)
    adapter.handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_free_room_blocklisted_bot_no_mention_ignored(monkeypatch):
    """Free room + blocklisted sender + no mention → ignored (the ambient
    bypass is suppressed for peer-bot identities)."""
    monkeypatch.delenv("MATRIX_REQUIRE_MENTION", raising=False)
    monkeypatch.setenv("MATRIX_FREE_RESPONSE_ROOMS", "!room1:example.org")
    monkeypatch.setenv(
        "MATRIX_FREE_ROOM_BOT_BLOCKLIST", "@field:example.org"
    )
    monkeypatch.setenv("MATRIX_AUTO_THREAD", "false")

    adapter = _make_adapter()
    event = _make_event(
        "peer bot top-level post, no mention", sender="@field:example.org"
    )

    await adapter._on_room_message(event)
    adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_free_room_blocklisted_bot_with_mention_still_engages(monkeypatch):
    """Free room + blocklisted sender + explicit ``@mention`` → engages.

    The blocklist only suppresses the *ambient bypass*. It does NOT prevent
    the bot from responding to a real ``@mention``. Pre-shipped room-mention
    pairing (commit ``c5edba1d7``) handles unauthorized senders via the
    pairing offer path; cross-agent mentions therefore stay routable.
    """
    monkeypatch.delenv("MATRIX_REQUIRE_MENTION", raising=False)
    monkeypatch.setenv("MATRIX_FREE_RESPONSE_ROOMS", "!room1:example.org")
    monkeypatch.setenv(
        "MATRIX_FREE_ROOM_BOT_BLOCKLIST", "@field:example.org"
    )
    monkeypatch.setenv("MATRIX_AUTO_THREAD", "false")

    adapter = _make_adapter()
    event = _make_event(
        "@hermes:example.org coord check", sender="@field:example.org"
    )

    await adapter._on_room_message(event)
    # The adapter routes this to handle_message because the require-mention
    # gate sees an explicit mention. Downstream auth/pairing decisions
    # (allowlist drop, room-mention-pairing offer) happen in the gateway
    # runner and are out of scope here.
    adapter.handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_free_room_human_no_mention_ignored(monkeypatch):
    """Non-free room + non-blocklisted sender + no mention → still ignored
    (the blocklist patch must NOT alter behavior outside free rooms)."""
    monkeypatch.delenv("MATRIX_REQUIRE_MENTION", raising=False)
    monkeypatch.setenv("MATRIX_FREE_RESPONSE_ROOMS", "!other:example.org")
    monkeypatch.setenv(
        "MATRIX_FREE_ROOM_BOT_BLOCKLIST", "@field:example.org"
    )
    monkeypatch.setenv("MATRIX_AUTO_THREAD", "false")

    adapter = _make_adapter()
    event = _make_event(
        "no mention in a normal room",
        sender="@alice:example.org",
        room_id="!room1:example.org",  # NOT in free rooms
    )

    await adapter._on_room_message(event)
    adapter.handle_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Init / config parsing
# ---------------------------------------------------------------------------


def test_blocklist_parsed_from_env(monkeypatch):
    """Env var is csv → set on the adapter."""
    monkeypatch.setenv(
        "MATRIX_FREE_ROOM_BOT_BLOCKLIST",
        "@field:example.org, @coord:example.org , @scribe:example.org",
    )

    adapter = _make_adapter()
    assert adapter._free_room_bot_blocklist == {
        "@field:example.org",
        "@coord:example.org",
        "@scribe:example.org",
    }


def test_blocklist_defaults_empty(monkeypatch):
    """No env var set → empty set; existing free-room behavior preserved."""
    monkeypatch.delenv("MATRIX_FREE_ROOM_BOT_BLOCKLIST", raising=False)

    adapter = _make_adapter()
    assert adapter._free_room_bot_blocklist == set()


def test_blocklist_parsed_from_extra():
    """Config extra (list form) is also accepted, mirroring free_rooms."""
    from plugins.platforms.matrix.adapter import MatrixAdapter

    config = PlatformConfig(
        enabled=True,
        token="syt_test_token",
        extra={
            "homeserver": "https://matrix.example.org",
            "user_id": "@hermes:example.org",
            "free_room_bot_blocklist": [
                "@field:example.org",
                "@coord:example.org",
            ],
        },
    )
    adapter = MatrixAdapter(config)
    assert adapter._free_room_bot_blocklist == {
        "@field:example.org",
        "@coord:example.org",
    }


# ---------------------------------------------------------------------------
# is_free_room public helper (used by gateway/run.py /sethome suppression)
# ---------------------------------------------------------------------------


def test_is_free_room_true_when_in_set(monkeypatch):
    monkeypatch.setenv(
        "MATRIX_FREE_RESPONSE_ROOMS",
        "!a:example.org,!b:example.org",
    )
    adapter = _make_adapter()
    assert adapter.is_free_room("!a:example.org") is True
    assert adapter.is_free_room("!b:example.org") is True


def test_is_free_room_false_when_not_in_set(monkeypatch):
    monkeypatch.setenv("MATRIX_FREE_RESPONSE_ROOMS", "!a:example.org")
    adapter = _make_adapter()
    assert adapter.is_free_room("!other:example.org") is False
    assert adapter.is_free_room("") is False
