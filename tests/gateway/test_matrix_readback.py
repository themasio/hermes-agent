"""Tests for Matrix TTS readback via 🔊 reaction.

Spec: matrix-hive repo docs/superpowers/specs/2026-05-18-tts-readback-design.md
"""
import asyncio
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip whole file if mautrix not installed (matches test_matrix_voice.py pattern).
try:
    import mautrix as _mautrix_probe
    if not isinstance(_mautrix_probe, types.ModuleType) or not hasattr(_mautrix_probe, "__file__"):
        pytest.skip("mautrix in sys.modules is a mock, not the real package", allow_module_level=True)
except ImportError:
    pytest.skip("mautrix not installed", allow_module_level=True)


# ---------------------------------------------------------------------------
# Adapter helpers
# ---------------------------------------------------------------------------

def _make_adapter(readback_enabled: bool = True, emoji: str = "🔊",
                  max_chars: int = 2000, timeout: int = 30):
    """Create a MatrixAdapter with mocked config, readback configurable."""
    from gateway.platforms.matrix import MatrixAdapter
    from gateway.config import PlatformConfig

    config = PlatformConfig(
        enabled=True,
        token="***",
        extra={
            "homeserver": "https://matrix.example.org",
            "user_id": "@coordinator:example.org",
            "readback_on_reaction": readback_enabled,
            "readback_trigger_emoji": emoji,
            "readback_max_chars": max_chars,
            "readback_timeout_seconds": timeout,
        },
    )
    adapter = MatrixAdapter(config)
    # Stub the client + helpers used by readback path
    adapter._client = AsyncMock()
    adapter._send_reaction = AsyncMock(return_value="$ack_event")
    adapter._redact_reaction = AsyncMock(return_value=True)
    adapter.send_voice = AsyncMock(return_value=SimpleNamespace(success=True))
    adapter.send_message = AsyncMock(return_value=SimpleNamespace(success=True))
    return adapter


def _make_reaction_event(key: str = "🔊", sender: str = "@alice:example.org",
                         room_id: str = "!room:example.org",
                         reacts_to: str = "$parent_msg",
                         reaction_id: str = "$reaction_evt"):
    """Build a mock m.reaction event in mautrix shape."""
    return SimpleNamespace(
        sender=sender,
        event_id=reaction_id,
        room_id=room_id,
        content={
            "m.relates_to": {
                "rel_type": "m.annotation",
                "event_id": reacts_to,
                "key": key,
            }
        },
    )


def _make_text_event(body: str = "hello world", msgtype: str = "m.text",
                     formatted_body: str = None):
    """Build a mock parent event returned by client.get_event."""
    content = {"msgtype": msgtype, "body": body}
    if formatted_body:
        content["formatted_body"] = formatted_body
        content["format"] = "org.matrix.custom.html"
    return SimpleNamespace(content=content)


# ---------------------------------------------------------------------------
# Smoke (sanity that scaffolding works)
# ---------------------------------------------------------------------------

def test_scaffold_can_make_adapter():
    """Scaffold sanity: adapter constructs and exposes readback state."""
    adapter = _make_adapter()
    assert adapter._readback_enabled is True
    assert adapter._readback_emoji == "🔊"


def test_ctor_reads_readback_flag_true():
    adapter = _make_adapter(readback_enabled=True)
    assert adapter._readback_enabled is True
    assert adapter._readback_emoji == "🔊"
    assert adapter._readback_max_chars == 2000
    assert adapter._readback_timeout_secs == 30
    assert adapter._readback_in_flight == set()


def test_ctor_readback_flag_defaults_false_when_missing():
    from gateway.platforms.matrix import MatrixAdapter
    from gateway.config import PlatformConfig
    config = PlatformConfig(
        enabled=True,
        token="***",
        extra={
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
        },
    )
    adapter = MatrixAdapter(config)
    assert adapter._readback_enabled is False
    assert adapter._readback_emoji == "🔊"
    assert adapter._readback_in_flight == set()
