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


@pytest.mark.asyncio
async def test_readback_happy_path_text():
    """Case 1 (spec §10.1): reacting 🔊 on m.text → TTS → voice reply."""
    adapter = _make_adapter(readback_enabled=True)
    adapter._client.get_event = AsyncMock(
        return_value=_make_text_event(body="hello world")
    )

    fake_tts_result = {"file_path": "/tmp/test.ogg", "duration_ms": 1234}
    with patch(
        "gateway.platforms.matrix.text_to_speech_tool",
        return_value=fake_tts_result,
    ) as tts_mock, patch(
        "gateway.platforms.matrix._strip_markdown_for_tts",
        side_effect=lambda t: t,
    ), patch("os.unlink"):
        await adapter._handle_readback_reaction(
            room_id="!room:example.org",
            parent_event_id="$parent_msg",
            sender="@alice:example.org",
        )

    # Ack 👂 was sent on parent
    adapter._send_reaction.assert_any_await(
        "!room:example.org", "$parent_msg", "👂"
    )
    # TTS was invoked with the text body
    tts_mock.assert_called_once()
    _, tts_kwargs = tts_mock.call_args
    assert tts_kwargs["text"] == "hello world"
    # Voice was sent as a reply to the parent
    adapter.send_voice.assert_awaited_once()
    _, voice_kwargs = adapter.send_voice.call_args
    assert voice_kwargs["audio_path"] == "/tmp/test.ogg"
    assert voice_kwargs["reply_to"] == "$parent_msg"
    assert voice_kwargs["chat_id"] == "!room:example.org"
    # In-flight set is cleared after completion
    assert "$parent_msg" not in adapter._readback_in_flight


@pytest.mark.asyncio
async def test_dispatch_skip_when_flag_disabled():
    """Case 2: flag off → no readback even if 🔊."""
    adapter = _make_adapter(readback_enabled=False)
    adapter._is_self_sender = MagicMock(return_value=False)
    adapter._is_duplicate_event = MagicMock(return_value=False)
    adapter._handle_readback_reaction = AsyncMock()
    event = _make_reaction_event(key="🔊")
    await adapter._on_reaction(event)
    adapter._handle_readback_reaction.assert_not_awaited()
    adapter._send_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_skip_when_wrong_emoji():
    """Case 3: flag on but emoji is 🔉 → fall through, no readback."""
    adapter = _make_adapter(readback_enabled=True)
    adapter._is_self_sender = MagicMock(return_value=False)
    adapter._is_duplicate_event = MagicMock(return_value=False)
    adapter._handle_readback_reaction = AsyncMock()
    event = _make_reaction_event(key="🔉")
    await adapter._on_reaction(event)
    adapter._handle_readback_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_skip_when_self_sender():
    """Case 4: bot's own 🔊 reaction → silent drop."""
    adapter = _make_adapter(readback_enabled=True)
    adapter._is_self_sender = MagicMock(return_value=True)
    adapter._is_duplicate_event = MagicMock(return_value=False)
    adapter._handle_readback_reaction = AsyncMock()
    event = _make_reaction_event(key="🔊", sender="@coordinator:example.org")
    await adapter._on_reaction(event)
    adapter._handle_readback_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_skip_when_approval_prompt_parent():
    """Case 5: parent is an unresolved approval prompt → in-branch guard
    blocks readback even when key=🔊. Approval flow handles the parent."""
    adapter = _make_adapter(readback_enabled=True)
    adapter._is_self_sender = MagicMock(return_value=False)
    adapter._is_duplicate_event = MagicMock(return_value=False)
    adapter._handle_readback_reaction = AsyncMock()
    # Insert an unresolved approval prompt keyed by the parent event id.
    fake_prompt = MagicMock(resolved=False, chat_id="!room:example.org")
    adapter._approval_prompts_by_event["$parent_msg"] = fake_prompt
    # Key IS 🔊 — readback branch is entered, but the in-branch approval
    # check (`approval is None or approval.resolved`) is False, so we
    # fall through to the existing approval flow without dispatching.
    event = _make_reaction_event(key="🔊")
    await adapter._on_reaction(event)
    await asyncio.sleep(0)
    adapter._handle_readback_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_invokes_handler_on_speaker_emoji():
    """Positive: flag on + 🔊 + non-self + no-approval-parent → handler IS awaited."""
    adapter = _make_adapter(readback_enabled=True)
    adapter._is_self_sender = MagicMock(return_value=False)
    adapter._is_duplicate_event = MagicMock(return_value=False)
    adapter._handle_readback_reaction = AsyncMock()
    event = _make_reaction_event(key="🔊")
    await adapter._on_reaction(event)
    # _on_reaction uses asyncio.create_task — yield once so the task runs.
    await asyncio.sleep(0)
    adapter._handle_readback_reaction.assert_awaited_once_with(
        "!room:example.org", "$parent_msg", "@alice:example.org"
    )


@pytest.mark.asyncio
async def test_extract_image_with_caption_reads_caption():
    """Case 12: m.image with a caption-shaped body → TTS reads it."""
    adapter = _make_adapter(readback_enabled=True)
    adapter._client.get_event = AsyncMock(
        return_value=_make_text_event(
            body="A photo of the hive in spring",
            msgtype="m.image",
        )
    )
    fake_tts_result = {"file_path": "/tmp/test.ogg", "duration_ms": 1234}
    with patch(
        "gateway.platforms.matrix.text_to_speech_tool",
        return_value=fake_tts_result,
    ) as tts_mock, patch("os.unlink"):
        await adapter._handle_readback_reaction(
            "!room:example.org", "$parent_msg", "@alice:example.org"
        )
    tts_mock.assert_called_once()
    assert tts_mock.call_args.kwargs["text"] == "A photo of the hive in spring"


@pytest.mark.asyncio
async def test_extract_image_filename_only_skipped():
    """Case 13: m.image body == filename → skip TTS (no caption-shaped text)."""
    adapter = _make_adapter(readback_enabled=True)
    adapter._client.get_event = AsyncMock(
        return_value=_make_text_event(
            body="IMG_1234.jpg",
            msgtype="m.image",
        )
    )
    with patch("gateway.platforms.matrix.text_to_speech_tool") as tts_mock:
        await adapter._handle_readback_reaction(
            "!room:example.org", "$parent_msg", "@alice:example.org"
        )
    tts_mock.assert_not_called()
    adapter.send_voice.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_audio_parent_skipped_silently():
    """Case 14: parent is m.audio → silent skip, no ⚠️."""
    adapter = _make_adapter(readback_enabled=True)
    adapter._client.get_event = AsyncMock(
        return_value=_make_text_event(body="voice.ogg", msgtype="m.audio")
    )
    with patch("gateway.platforms.matrix.text_to_speech_tool") as tts_mock:
        await adapter._handle_readback_reaction(
            "!room:example.org", "$parent_msg", "@alice:example.org"
        )
    tts_mock.assert_not_called()
    adapter.send_voice.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_html_formatted_body_strips_tags():
    """formatted_body present → HTML stripped before markdown strip."""
    adapter = _make_adapter(readback_enabled=True)
    adapter._client.get_event = AsyncMock(
        return_value=_make_text_event(
            body="raw body",
            msgtype="m.text",
            formatted_body="<b>raw body</b> <a href='x'>link</a>",
        )
    )
    fake_tts_result = {"file_path": "/tmp/test.ogg", "duration_ms": 1234}
    with patch(
        "gateway.platforms.matrix.text_to_speech_tool",
        return_value=fake_tts_result,
    ) as tts_mock, patch(
        "gateway.platforms.matrix._strip_markdown_for_tts",
        side_effect=lambda t: t,
    ), patch("os.unlink"):
        await adapter._handle_readback_reaction(
            "!room:example.org", "$parent_msg", "@alice:example.org"
        )
    tts_mock.assert_called_once()
    text = tts_mock.call_args.kwargs["text"]
    assert "<b>" not in text and "</b>" not in text
    assert "raw body link" in text or "raw body" in text


@pytest.mark.asyncio
async def test_markdown_stripped_before_tts(monkeypatch):
    """Case 11: bold/link markdown removed before TTS."""
    adapter = _make_adapter(readback_enabled=True)
    adapter._client.get_event = AsyncMock(
        return_value=_make_text_event(body="**bold** and [link](https://x)")
    )

    # Use the real _strip_markdown_for_tts behavior to assert end-to-end.
    fake_tts_result = {"file_path": "/tmp/test.ogg", "duration_ms": 1234}
    with patch(
        "gateway.platforms.matrix.text_to_speech_tool",
        return_value=fake_tts_result,
    ) as tts_mock, patch("os.unlink"):
        await adapter._handle_readback_reaction(
            "!room:example.org", "$parent_msg", "@alice:example.org"
        )
    tts_text = tts_mock.call_args.kwargs["text"]
    assert "**" not in tts_text
    assert "](" not in tts_text   # markdown link syntax gone
    assert "bold" in tts_text
    assert "link" in tts_text
