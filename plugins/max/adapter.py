"""MAX Bot API v2 platform adapter for Hermes Agent.

This module is intentionally a third-party plugin boundary.  It does not add
MAX to Hermes core, so upgrading Hermes replaces the core installation without
overwriting this adapter.  Transport is handled by :mod:`.client`; the
adapter only translates MAX events into Hermes' normalized event contract.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Mapping, Optional

try:
    from gateway.config import Platform, PlatformConfig
    from gateway.platforms.base import (
        BasePlatformAdapter,
        MessageEvent,
        MessageType,
        SendResult,
    )
except ImportError:  # pragma: no cover - used only by standalone unit tests
    @dataclass
    class _FallbackMessageEvent:
        text: str
        message_type: Any = None
        source: Any = None
        raw_message: Any = None
        message_id: Optional[str] = None
        media_urls: List[str] = None
        media_types: List[str] = None
        reply_to_message_id: Optional[str] = None
        metadata: Dict[str, Any] = None

    @dataclass
    class _FallbackSendResult:
        success: bool
        message_id: Optional[str] = None
        error: Optional[str] = None
        raw_response: Any = None
        retryable: bool = False
        retry_after: Optional[float] = None
        continuation_message_ids: tuple = ()

    class _FallbackBase:
        def __init__(self, config: Any, platform: Any) -> None:
            self.config = config
            self.platform = platform
            self._running = False

        def build_source(self, **kwargs: Any) -> Any:
            return SimpleNamespace(platform=self.platform, **kwargs)

        def _mark_connected(self) -> None:
            self._running = True

        def _mark_disconnected(self) -> None:
            self._running = False

        async def handle_message(self, _event: Any) -> None:
            return None

    BasePlatformAdapter = _FallbackBase  # type: ignore[misc,assignment]
    Platform = None  # type: ignore[assignment]
    PlatformConfig = Any  # type: ignore[assignment,misc]
    MessageEvent = _FallbackMessageEvent  # type: ignore[assignment]
    MessageType = SimpleNamespace(
        TEXT="text", COMMAND="command", PHOTO="photo", VOICE="voice", DOCUMENT="document"
    )
    SendResult = _FallbackSendResult  # type: ignore[assignment]

from .client import DEFAULT_API_BASE, DEFAULT_MEDIA_MAX_BYTES, MaxApiError, MaxClient
from .common import AccessPolicy, split_message
from .interactive import MaxCallbackStore, build_inline_keyboard
from .media import (
    attachment_from_payload,
    media_type_for_file,
    mime_type_for_file,
)
from .models import MaxCallback, MaxMessage
from .polling_state import MaxTargetStore, PollingMarkerStore
from .rate_limit import MAX_MESSAGE_LENGTH, MaxRateLimiter, with_backoff
from .tls import tls_verify_from_env
from .webhook import MaxWebhookReceiver, WebhookResult

logger = logging.getLogger(__name__)

PLATFORM_NAME = "max"
PLATFORM_LABEL = "MAX Messenger"
PLATFORM_EMOJI = "рџ’¬"
PLATFORM_HINT = (
    "You are chatting via MAX. Keep a response within MAX's 4000-character message limit. "
    "Use Markdown only where it improves readability; availability depends on the configured "
    "MAX client and network, not on a universal whitelist guarantee."
)
MAX_UPDATE_TYPES = (
    "message_created",
    "message_callback",
    "bot_started",
    "bot_stopped",
    "dialog_removed",
)


def _is_max_rate_limit(exc: BaseException) -> bool:
    return getattr(exc, "status_code", None) == 429 or getattr(exc, "code", None) in {
        "too_many_requests",
        "rate_limit",
    }


def _retry_after(exc: BaseException) -> Optional[float]:
    value = getattr(exc, "retry_after", None)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_media_send_retryable(exc: BaseException) -> bool:
    return _is_max_rate_limit(exc) or getattr(exc, "code", None) in {
        "attachment.not.ready",
        "attachment_not_ready",
    }


def _media_retry_after(exc: BaseException) -> Optional[float]:
    retry_after = _retry_after(exc)
    if retry_after is not None:
        return retry_after
    if getattr(exc, "code", None) in {"attachment.not.ready", "attachment_not_ready"}:
        return 1.0
    return None


def _message_type(message: MaxMessage) -> Any:
    if _is_command(message.text):
        return getattr(MessageType, "COMMAND", getattr(MessageType, "TEXT", None))
    for attachment in message.attachments:
        kind = str(attachment.get("type") or "").lower()
        if kind == "image":
            return getattr(MessageType, "PHOTO", getattr(MessageType, "TEXT", None))
        if kind in {"audio", "voice"}:
            return getattr(MessageType, "VOICE", getattr(MessageType, "TEXT", None))
        if kind in {"video", "file"}:
            return getattr(MessageType, "DOCUMENT", getattr(MessageType, "TEXT", None))
    return getattr(MessageType, "TEXT", None)


def _reply_message_id(link: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not link:
        return None
    for key in ("message", "message_id", "mid"):
        value = link.get(key)
        if value:
            return str(value)
    return None


def _reply_link(reply_to: Optional[str]) -> Optional[dict[str, str]]:
    return {"type": "reply", "mid": str(reply_to)} if reply_to else None


def _append_event_note(existing: Optional[str], note: str) -> str:
    if not note:
        return existing or ""
    if not existing:
        return note
    return f"{existing}\n\n{note}"


def _cache_media_bytes(
    data: bytes,
    *,
    filename: str,
    mime_type: str,
    default_kind: str,
) -> Any:
    """Delegate media persistence to Hermes without importing it at module load."""

    from gateway.platforms.base import cache_media_bytes

    return cache_media_bytes(
        data,
        filename=filename,
        mime_type=mime_type,
        default_kind=default_kind,
    )


def _build_message_event(
    adapter: "MaxAdapter",
    message: MaxMessage,
    *,
    text: Optional[str] = None,
    media_urls: Optional[List[str]] = None,
    media_types: Optional[List[str]] = None,
) -> Any:
    """Translate a normalized MAX message through Hermes' public contract."""

    chat_type = "group" if message.is_group else "dm"
    source = adapter.build_source(
        chat_id=message.chat_id,
        chat_name=message.chat_title or message.user_name,
        chat_type=chat_type,
        user_id=message.user_id,
        user_name=message.user_name,
        message_id=message.message_id,
        role_authorized=False,
    )
    return MessageEvent(
        text=message.text if text is None else text,
        message_type=_message_type(message),
        source=source,
        raw_message=message.raw_message,
        message_id=message.message_id,
        reply_to_message_id=_reply_message_id(message.link),
        media_urls=list(media_urls or []),
        media_types=list(media_types or []),
        metadata={
            "max_chat_type": message.chat_type,
            "max_user_id": message.user_id,
        },
    )


def _is_command(text: str) -> bool:
    return bool(text) and text.lstrip().startswith("/")


def _response_message_id(response: Mapping[str, Any]) -> Optional[str]:
    message = response.get("message")
    if isinstance(message, Mapping):
        body = message.get("body")
        if isinstance(body, Mapping) and body.get("mid"):
            return str(body["mid"])
    for key in ("message_id", "id"):
        if response.get(key):
            return str(response[key])
    return None


def _send_result_from_ids(message_ids: Iterable[str]) -> Any:
    ids = [str(item) for item in message_ids if item]
    if not ids:
        return SendResult(success=True)
    return SendResult(
        success=True,
        message_id=ids[-1],
        continuation_message_ids=tuple(ids[:-1]),
    )


def _config_extra(config: Any) -> dict[str, Any]:
    value = getattr(config, "extra", {}) or {}
    return dict(value) if isinstance(value, Mapping) else {}


class MaxAdapter(BasePlatformAdapter):  # type: ignore[misc]
    """Webhook-first MAX adapter using Hermes' external plugin contract."""

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH
    splits_long_messages = True
    supports_code_blocks = False
    SUPPORTS_MESSAGE_EDITING = True

    def __init__(self, config: "PlatformConfig") -> None:
        platform = Platform(PLATFORM_NAME) if Platform is not None else PLATFORM_NAME
        super().__init__(config, platform)  # type: ignore[arg-type]
        self._config = config
        self._extra = _config_extra(config)
        self._token = str(
            getattr(config, "token", None)
            or self._extra.get("token")
            or os.environ.get("MAX_BOT_TOKEN", "")
        ).strip()
        self._api_base = str(
            self._extra.get("api_base_url")
            or os.environ.get("MAX_API_BASE_URL", DEFAULT_API_BASE)
        ).rstrip("/")
        self._webhook_url = str(
            self._extra.get("webhook_url") or os.environ.get("MAX_WEBHOOK_URL", "")
        ).strip() or None
        self._webhook_secret = str(
            self._extra.get("webhook_secret") or os.environ.get("MAX_WEBHOOK_SECRET", "")
        ).strip() or None
        self._polling_timeout = int(self._extra.get("polling_timeout", 30))
        self._access = AccessPolicy.from_env_and_extra(
            allowed_users_env=os.environ.get("MAX_ALLOWED_USERS"),
            group_allowed_users_env=os.environ.get("MAX_GROUP_ALLOWED_USERS"),
            group_allowed_chats_env=os.environ.get("MAX_GROUP_ALLOWED_CHATS"),
            allow_all_env=os.environ.get("MAX_ALLOW_ALL_USERS"),
            guest_mode_env=os.environ.get("MAX_GUEST_MODE"),
            extra=self._extra,
        )
        self._require_mention = _truthy(
            self._extra.get("require_mention", os.environ.get("MAX_REQUIRE_MENTION", "true"))
        )
        self._client: Optional[MaxClient] = None
        self._receiver: Optional[MaxWebhookReceiver] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._bot_user_id: Optional[str] = None
        self._bot_username: Optional[str] = None
        self._chat_target_types: dict[str, str] = {}
        self._webhook_mode = bool(self._webhook_url)
        default_inbox = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
        self._inbox_path = str(
            self._extra.get("inbox_path")
            or os.environ.get("MAX_INBOX_PATH", default_inbox / "max" / "webhook-inbox.sqlite3")
        )
        self._marker_path = str(
            self._extra.get("marker_path")
            or os.environ.get("MAX_MARKER_PATH", default_inbox / "max" / "polling-marker.sqlite3")
        )
        self._target_path = str(
            self._extra.get("target_path")
            or os.environ.get("MAX_TARGET_PATH", default_inbox / "max" / "targets.sqlite3")
        )
        self._marker_store: Optional[PollingMarkerStore] = None
        self._target_store: Optional[MaxTargetStore] = None
        self._rate_limiter = MaxRateLimiter()
        try:
            callback_ttl = float(
                self._extra.get(
                    "callback_ttl_seconds",
                    os.environ.get("MAX_CALLBACK_TTL_SECONDS", "600"),
                )
            )
        except (TypeError, ValueError):
            callback_ttl = 600.0
        self._callbacks = MaxCallbackStore(ttl_seconds=callback_ttl)
        self._model_pickers: dict[str, dict[str, Any]] = {}
        try:
            self._media_max_bytes = int(
                self._extra.get(
                    "media_max_bytes",
                    os.environ.get("MAX_MEDIA_MAX_BYTES", str(DEFAULT_MEDIA_MAX_BYTES)),
                )
            )
        except (TypeError, ValueError):
            self._media_max_bytes = DEFAULT_MEDIA_MAX_BYTES
        if self._media_max_bytes <= 0:
            self._media_max_bytes = DEFAULT_MEDIA_MAX_BYTES
        self._lock_acquired = False

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not self._token:
            logger.error("MAX_BOT_TOKEN is not configured")
            return False
        try:
            acquire_lock = getattr(self, "_acquire_platform_lock", None)
            if callable(acquire_lock):
                if not acquire_lock("max_bot_token", self._token, "MAX bot token"):
                    return False
                self._lock_acquired = True
            verify = tls_verify_from_env(
                {"MAX_CA_BUNDLE": str(self._extra.get("ca_bundle", os.environ.get("MAX_CA_BUNDLE", "")))}
            )
            self._client = MaxClient(self._token, base_url=self._api_base, verify=verify)
            bot = await self._client.get_me()
            self._bot_user_id = str(bot.get("user_id") or bot.get("id") or "") or None
            self._bot_username = str(bot.get("username") or "") or None
            self._running = True
            self._target_store = MaxTargetStore(self._target_path)

            if self._webhook_mode:
                if not self._webhook_secret:
                    raise ValueError("MAX_WEBHOOK_SECRET is required in Webhook mode")
                self._receiver = MaxWebhookReceiver(
                    self._webhook_secret,
                    inbox_path=self._inbox_path,
                )
                await self._client.subscribe_webhook(
                    self._webhook_url or "",
                    self._webhook_secret,
                    update_types=MAX_UPDATE_TYPES,
                )
                self._worker_task = asyncio.create_task(
                    self._consume_webhook_queue(), name="max-webhook-worker"
                )
            else:
                self._marker_store = PollingMarkerStore(self._marker_path)
                self._polling_task = asyncio.create_task(
                    self._poll_updates(), name="max-polling"
                )
            self._mark_connected()
            logger.info(
                "MAX adapter connected transport=%s reconnect=%s",
                "webhook" if self._webhook_mode else "polling",
                is_reconnect,
            )
            return True
        except (MaxApiError, ValueError, OSError) as exc:
            logger.error("MAX adapter connection failed: %s", exc)
            await self.disconnect()
            return False

    async def disconnect(self) -> None:
        self._running = False
        for task in (self._polling_task, self._worker_task):
            if task and not task.done():
                task.cancel()
        for task in (self._polling_task, self._worker_task):
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._polling_task = None
        self._worker_task = None
        self._model_pickers.clear()
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._receiver is not None:
            await self._receiver.close()
        self._receiver = None
        if self._marker_store is not None:
            self._marker_store.close()
            self._marker_store = None
        if self._target_store is not None:
            self._target_store.close()
            self._target_store = None
        if self._lock_acquired:
            release_lock = getattr(self, "_release_platform_lock", None)
            if callable(release_lock):
                release_lock()
            self._lock_acquired = False
        try:
            self._mark_disconnected()
        except AttributeError:
            pass

    async def _poll_updates(self) -> None:
        marker: Optional[int] = self._marker_store.get() if self._marker_store else None
        backoff = 1.0
        while self._running and self._client is not None:
            try:
                result = await self._client.get_updates(
                    marker=marker,
                    timeout=self._polling_timeout,
                    types=MAX_UPDATE_TYPES,
                )
                marker = result.get("marker", marker)
                if marker is not None and self._marker_store is not None:
                    self._marker_store.set(int(marker))
                backoff = 1.0
                for update in result.get("updates", []) or []:
                    if isinstance(update, Mapping):
                        await self._dispatch_update(update)
            except asyncio.CancelledError:
                return
            except MaxApiError as exc:
                if not self._running:
                    return
                delay = exc.retry_after or min(backoff, 30.0)
                logger.warning("MAX polling failed; retrying in %.1fs: %s", delay, exc)
                await asyncio.sleep(delay)
                backoff = min(backoff * 2.0, 30.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("MAX polling failed; retrying in %.1fs: %s", backoff, exc)
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2.0, 30.0)

    async def _consume_webhook_queue(self) -> None:
        if self._receiver is None:
            return
        while self._running:
            try:
                from_queue = True
                try:
                    update = await asyncio.wait_for(self._receiver.get_queued(), timeout=1.0)
                except asyncio.TimeoutError:
                    pending = await self._receiver.pending_updates(limit=1)
                    if not pending:
                        continue
                    update = pending[0]
                    from_queue = False
                try:
                    await self._dispatch_update(update)
                    await self._receiver.mark_processed(update)
                except Exception:
                    await self._receiver.mark_failed(update)
                    raise
                finally:
                    if from_queue:
                        self._receiver.queue.task_done()
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                logger.exception("MAX Webhook update processing failed")
                await asyncio.sleep(1.0)

    async def handle_webhook(
        self, headers: Mapping[str, str], update: Mapping[str, Any]
    ) -> WebhookResult:
        """Accept one Webhook update for a reverse proxy/ASGI bridge.

        The method returns a status decision; the HTTP server must send that
        status immediately and leave model processing to the worker.
        """

        if self._receiver is None:
            return WebhookResult(status_code=503, accepted=False)
        return await self._receiver.receive(headers, update)

    async def _populate_message_media(self, message: MaxMessage, event: Any) -> None:
        """Download inbound MAX attachments into Hermes' local media cache."""

        if self._client is None:
            return
        for raw_attachment in message.attachments:
            attachment = attachment_from_payload(raw_attachment)
            if attachment is None:
                continue
            if not attachment.url:
                event.text = _append_event_note(
                    event.text,
                    f"[MAX attachment '{attachment.filename}' has no downloadable URL.]",
                )
                continue
            try:
                data, remote_mime = await self._client.download_…5511 tokens truncated…ping[str, Any]:
                return await self._client.send_message(
                    chat_id,
                    chunk,
                    target_type=target_type,
                    link=link,
                    attachments=chunk_attachments,
                )

            try:
                response = await with_backoff(
                    _send,
                    is_rate_limit=_is_media_send_retryable,
                    extract_retry_after=_media_retry_after,
                    max_attempts=5,
                )
            except MaxApiError as exc:
                return SendResult(
                    success=False,
                    error=str(exc),
                    retryable=exc.retryable,
                    retry_after=exc.retry_after,
                )
            message_id = _response_message_id(response)
            if message_id:
                message_ids.append(message_id)
        return _send_result_from_ids(message_ids)

    async def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: Optional[list],
        clarify_id: str,
        session_key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if not choices:
            return await self.send(chat_id, f"вќ“ {question}", metadata=metadata)

        context = self._interactive_context(chat_id, metadata)
        if context is None:
            return SendResult(
                success=False,
                error="MAX native buttons require a user-bound direct message",
            )
        _target_type, user_id = context
        rows: list[list[Mapping[str, str]]] = []
        option_lines = [f"{index + 1}. {choice}" for index, choice in enumerate(choices)]
        for index, _choice in enumerate(choices):
            payload = self._callbacks.issue(
                "clarify",
                f"{clarify_id}:{index}",
                user_id=user_id,
                chat_id=chat_id,
                session_key=session_key,
            )
            rows.append([{"type": "callback", "text": str(index + 1), "payload": payload}])
        other_payload = self._callbacks.issue(
            "clarify",
            f"{clarify_id}:other",
            user_id=user_id,
            chat_id=chat_id,
            session_key=session_key,
        )
        rows.append([{"type": "callback", "text": "Р”СЂСѓРіРѕРµ", "payload": other_payload}])
        return await self._send_interactive_prompt(
            chat_id,
            "вќ“ " + str(question) + "\n\n" + "\n".join(option_lines),
            rows,
            metadata=metadata,
        )

    async def send_exec_approval(
        self,
        chat_id: str,
        command: str,
        session_key: str,
        description: str = "dangerous command",
        metadata: Optional[Dict[str, Any]] = None,
        allow_permanent: bool = True,
        allow_session: bool = True,
        smart_denied: bool = False,
    ) -> Any:
        del smart_denied
        context = self._interactive_context(chat_id, metadata)
        if context is None:
            return SendResult(
                success=False,
                error="MAX native buttons require a user-bound direct message",
            )
        _target_type, user_id = context
        choices: list[tuple[str, str]] = [("once", "Р Р°Р·СЂРµС€РёС‚СЊ РѕРґРёРЅ СЂР°Р·")]
        if allow_session:
            choices.append(("session", "Р Р°Р·СЂРµС€РёС‚СЊ РЅР° СЃРµСЃСЃРёСЋ"))
        if allow_permanent:
            choices.append(("always", "Р Р°Р·СЂРµС€РёС‚СЊ РІСЃРµРіРґР°"))
        choices.append(("deny", "Р—Р°РїСЂРµС‚РёС‚СЊ"))
        rows: list[list[Mapping[str, str]]] = []
        for index in range(0, len(choices), 2):
            row: list[Mapping[str, str]] = []
            for choice, label in choices[index : index + 2]:
                payload = self._callbacks.issue(
                    "approval",
                    choice,
                    user_id=user_id,
                    chat_id=chat_id,
                    session_key=session_key,
                )
                row.append({"type": "callback", "text": label, "payload": payload})
            rows.append(row)
        preview = str(command)
        if len(preview) > 3200:
            preview = preview[:3200] + "..."
        text = f"вљ пёЏ РўСЂРµР±СѓРµС‚СЃСЏ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ РєРѕРјР°РЅРґС‹:\n\n```\n{preview}\n```\nРџСЂРёС‡РёРЅР°: {description}"
        return await self._send_interactive_prompt(chat_id, text, rows, metadata=metadata)

    async def send_slash_confirm(
        self,
        chat_id: str,
        title: str,
        message: str,
        session_key: str,
        confirm_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        context = self._interactive_context(chat_id, metadata)
        if context is None:
            return SendResult(
                success=False,
                error="MAX native buttons require a user-bound direct message",
            )
        _target_type, user_id = context
        choices = (
            ("once", "РџРѕРґС‚РІРµСЂРґРёС‚СЊ РѕРґРёРЅ СЂР°Р·"),
            ("always", "РџРѕРґС‚РІРµСЂР¶РґР°С‚СЊ РІСЃРµРіРґР°"),
            ("cancel", "РћС‚РјРµРЅР°"),
        )
        rows: list[list[Mapping[str, str]]] = []
        for choice, label in choices:
            payload = self._callbacks.issue(
                "slash",
                f"{choice}:{confirm_id}",
                user_id=user_id,
                chat_id=chat_id,
                session_key=session_key,
            )
            rows.append([{"type": "callback", "text": label, "payload": payload}])
        return await self._send_interactive_prompt(
            chat_id,
            f"**{title}**\n\n{message}",
            rows,
            metadata=metadata,
        )

    async def send_model_picker(
        self,
        chat_id: str,
        providers: list,
        current_model: str,
        current_provider: str,
        session_key: str,
        on_model_selected,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        context = self._interactive_context(chat_id, metadata)
        if context is None:
            return SendResult(
                success=False,
                error="MAX native buttons require a user-bound direct message",
            )
        _target_type, user_id = context
        normalized = [item for item in providers if isinstance(item, Mapping)]
        if not normalized:
            return SendResult(success=False, error="MAX model provider list is empty")

        picker_id = token_urlsafe(9)
        self._model_pickers[picker_id] = {
            "providers": normalized,
            "current_model": str(current_model or ""),
            "current_provider": str(current_provider or ""),
            "selected_provider": "",
            "selected_provider_name": "",
            "model_list": [],
            "on_model_selected": on_model_selected,
            "session_key": session_key,
            "user_id": user_id,
            "chat_id": str(chat_id),
        }
        state = self._model_pickers[picker_id]
        rows = self._model_provider_rows(state, picker_id, None)
        result = await self._send_interactive_prompt(
            chat_id,
            self._model_picker_provider_text(state),
            rows,
            metadata=metadata,
        )
        if not getattr(result, "success", False):
            self._model_pickers.pop(picker_id, None)
        return result

    def _model_provider_rows(
        self,
        state: Mapping[str, Any],
        picker_id: str,
        callback: Optional[MaxCallback],
    ) -> list[list[Mapping[str, str]]]:
        chat_id = callback.chat_id if callback else ""
        user_id = callback.user_id if callback else str(state["user_id"])
        rows: list[list[Mapping[str, str]]] = []
        buttons: list[Mapping[str, str]] = []
        for provider in state["providers"][:20]:
            slug = str(provider.get("slug") or "").strip()
            if not slug:
                continue
            name = str(provider.get("name") or slug)
            count = provider.get("total_models", len(provider.get("models", [])))
            label = f"{name} ({count})"
            if slug == state.get("current_provider"):
                label = f"вњ“ {label}"
            payload = self._callbacks.issue(
                "model",
                f"{picker_id}:provider:{slug}",
                user_id=user_id,
                chat_id=chat_id or str(state.get("chat_id") or "user"),
                session_key=str(state.get("session_key") or ""),
            )
            buttons.append({"type": "callback", "text": label[:60], "payload": payload})
        for index in range(0, len(buttons), 2):
            rows.append(buttons[index : index + 2])
        rows.append([self._model_cancel_button(picker_id, user_id, chat_id, state)])
        return rows

    def _model_rows(
        self,
        state: Mapping[str, Any],
        picker_id: str,
        callback: MaxCallback,
    ) -> list[list[Mapping[str, str]]]:
        rows: list[list[Mapping[str, str]]] = []
        buttons: list[Mapping[str, str]] = []
        for index, model_id in enumerate(state.get("model_list", [])[:50]):
            label = str(model_id).rsplit("/", 1)[-1]
            if len(label) > 40:
                label = label[:37] + "..."
            payload = self._callbacks.issue(
                "model",
                f"{picker_id}:model:{index}",
                user_id=callback.user_id,
                chat_id=callback.chat_id,
                session_key=str(state.get("session_key") or ""),
            )
            buttons.append({"type": "callback", "text": label, "payload": payload})
        for index in range(0, len(buttons), 2):
            rows.append(buttons[index : index + 2])
        back_payload = self._callbacks.issue(
            "model",
            f"{picker_id}:back",
            user_id=callback.user_id,
            chat_id=callback.chat_id,
            session_key=str(state.get("session_key") or ""),
        )
        rows.append([{"type": "callback", "text": "РќР°Р·Р°Рґ", "payload": back_payload}])
        rows.append([self._model_cancel_button(picker_id, callback.user_id, callback.chat_id, state)])
        return rows

    def _model_cancel_button(
        self,
        picker_id: str,
        user_id: str,
        chat_id: str,
        state: Mapping[str, Any],
    ) -> Mapping[str, str]:
        payload = self._callbacks.issue(
            "model",
            f"{picker_id}:cancel",
            user_id=user_id,
            chat_id=chat_id or str(state.get("chat_id") or "user"),
            session_key=str(state.get("session_key") or ""),
        )
        return {"type": "callback", "text": "РћС‚РјРµРЅР°", "payload": payload}

    @staticmethod
    def _model_picker_provider_text(state: Mapping[str, Any]) -> str:
        model = state.get("current_model") or "РЅРµРёР·РІРµСЃС‚РЅР°"
        provider = state.get("current_provider") or "РЅРµРёР·РІРµСЃС‚РµРЅ"
        return f"вљ™пёЏ РќР°СЃС‚СЂРѕР№РєР° РјРѕРґРµР»Рё\n\nРўРµРєСѓС‰Р°СЏ РјРѕРґРµР»СЊ: `{model}`\nРџСЂРѕРІР°Р№РґРµСЂ: {provider}\n\nР’С‹Р±РµСЂРёС‚Рµ РїСЂРѕРІР°Р№РґРµСЂР°:"

    @staticmethod
    def _model_picker_model_text(state: Mapping[str, Any]) -> str:
        provider = state.get("selected_provider_name") or state.get("selected_provider")
        models = state.get("model_list", [])
        return f"вљ™пёЏ РќР°СЃС‚СЂРѕР№РєР° РјРѕРґРµР»Рё\n\nРџСЂРѕРІР°Р№РґРµСЂ: {provider}\nР”РѕСЃС‚СѓРїРЅРѕ РјРѕРґРµР»РµР№: {len(models)}\n\nР’С‹Р±РµСЂРёС‚Рµ РјРѕРґРµР»СЊ:"

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> Any:
        del chat_id, finalize
        if self._client is None:
            return SendResult(success=False, error="MAX adapter is not connected")
        try:
            await self._rate_limiter.acquire(chat_id)
            response = await self._client.edit_message(message_id, content[:MAX_MESSAGE_LENGTH])
        except MaxApiError as exc:
            return SendResult(
                success=False,
                error=str(exc),
                retryable=exc.retryable,
                retry_after=exc.retry_after,
            )
        return SendResult(success=True, message_id=message_id, raw_response=response)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        target_type = self._chat_target_types.get(chat_id, "user")
        return {"name": chat_id, "type": "group" if target_type == "chat" else "dm", "chat_id": chat_id}


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def check_requirements() -> bool:
    try:
        import httpx  # noqa: F401
    except ImportError:
        return False
    return True


def validate_config(config: Any) -> bool:
    return bool(
        str(getattr(config, "token", None) or os.environ.get("MAX_BOT_TOKEN", "")).strip()
    )


def is_connected(config: Any) -> bool:
    return validate_config(config)


def env_enablement() -> Optional[Dict[str, Any]]:
    token = os.environ.get("MAX_BOT_TOKEN", "").strip()
    if not token:
        return None
    seed: Dict[str, Any] = {"enabled": True, "token": token}
    home = os.environ.get("MAX_HOME_CHANNEL", "").strip()
    if home:
        seed["home_channel"] = {"chat_id": home, "name": home}
    return seed


def apply_yaml_config(_yaml_cfg: dict, platform_cfg: dict) -> dict[str, Any]:
    """Map plugin-owned ``platforms.max`` keys without overriding env vars."""

    cfg = platform_cfg if isinstance(platform_cfg, Mapping) else {}
    extra = cfg.get("extra") if isinstance(cfg.get("extra"), Mapping) else cfg
    result: dict[str, Any] = {}
    env_map = {
        "api_base_url": "MAX_API_BASE_URL",
        "webhook_url": "MAX_WEBHOOK_URL",
        "webhook_secret": "MAX_WEBHOOK_SECRET",
        "ca_bundle": "MAX_CA_BUNDLE",
        "callback_ttl_seconds": "MAX_CALLBACK_TTL_SECONDS",
        "media_max_bytes": "MAX_MEDIA_MAX_BYTES",
        "require_mention": "MAX_REQUIRE_MENTION",
        "allow_from": "MAX_ALLOWED_USERS",
        "group_allow_from": "MAX_GROUP_ALLOWED_USERS",
        "group_allowed_chats": "MAX_GROUP_ALLOWED_CHATS",
    }
    for key, value in extra.items():
        if key in {"enabled", "token"}:
            continue
        env_name = env_map.get(key)
        if env_name and not os.environ.get(env_name):
            os.environ[env_name] = str(value).lower() if isinstance(value, bool) else str(value)
        if key in {
            "api_base_url",
            "webhook_url",
            "webhook_secret",
            "ca_bundle",
            "callback_ttl_seconds",
            "media_max_bytes",
            "require_mention",
        }:
            result[key] = os.environ.get(env_name, value) if env_name else value
        elif key in {"allow_from", "group_allow_from", "group_allowed_chats"}:
            result[key] = value
    return result


async def standalone_send(
    pconfig: Any,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    del thread_id
    token = str(getattr(pconfig, "token", None) or os.environ.get("MAX_BOT_TOKEN", "")).strip()
    if not token:
        return {"error": "MAX_BOT_TOKEN is not configured"}
    extra = _config_extra(pconfig)
    try:
        verify = tls_verify_from_env(
            {"MAX_CA_BUNDLE": str(extra.get("ca_bundle", os.environ.get("MAX_CA_BUNDLE", "")))}
        )
        client = MaxClient(
            token,
            base_url=str(extra.get("api_base_url") or os.environ.get("MAX_API_BASE_URL", DEFAULT_API_BASE)),
            verify=verify,
        )
        try:
            target_type = "chat" if str(chat_id) in set(extra.get("group_allowed_chats", [])) else "user"
            target_path = str(
                extra.get("target_path")
                or os.environ.get("MAX_TARGET_PATH", Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "max" / "targets.sqlite3")
            )
            target_store = MaxTargetStore(target_path)
            try:
                target_type = target_store.get(str(chat_id)) or target_type
            finally:
                target_store.close()
            try:
                media_max_bytes = int(
                    extra.get(
                        "media_max_bytes",
                        os.environ.get("MAX_MEDIA_MAX_BYTES", str(DEFAULT_MEDIA_MAX_BYTES)),
                    )
                )
            except (TypeError, ValueError):
                media_max_bytes = DEFAULT_MEDIA_MAX_BYTES
            attachments: list[Mapping[str, Any]] = []
            for media_path in media_files or []:
                path = str(media_path)
                media_type = media_type_for_file(path, force_document=force_document)
                upload = await client.upload_media(
                    path,
                    media_type=media_type,
                    max_bytes=media_max_bytes,
                    mime_type=mime_type_for_file(path, media_type),
                )
                token_value = str(upload.get("token") or "").strip()
                if not token_value:
                    raise MaxApiError("MAX media upload returned no attachment token")
                attachments.append({"type": media_type, "payload": {"token": token_value}})
            last_id = None
            chunks = split_message(message, MAX_MESSAGE_LENGTH) if message else [""]
            for index, chunk in enumerate(chunks):
                response = await with_backoff(
                    lambda: client.send_message(
                        str(chat_id),
                        chunk,
                        target_type=target_type,
                        attachments=attachments if index == 0 else None,
                    ),
                    is_rate_limit=_is_media_send_retryable,
                    extract_retry_after=_media_retry_after,
                    max_attempts=5,
                )
                last_id = _response_message_id(response)
            return {"success": True, "platform": PLATFORM_NAME, "chat_id": str(chat_id), "message_id": last_id}
        finally:
            await client.close()
    except (MaxApiError, ValueError, OSError) as exc:
        return {"error": f"MAX standalone send failed: {exc}"}


def register(ctx: Any) -> None:
    ctx.register_platform(
        name=PLATFORM_NAME,
        label=PLATFORM_LABEL,
        adapter_factory=MaxAdapter,
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        env_enablement_fn=env_enablement,
        apply_yaml_config_fn=apply_yaml_config,
        cron_deliver_env_var="MAX_HOME_CHANNEL",
        standalone_sender_fn=standalone_send,
        allowed_users_env="MAX_ALLOWED_USERS",
        allow_all_env="MAX_ALLOW_ALL_USERS",
        max_message_length=MAX_MESSAGE_LENGTH,
        emoji=PLATFORM_EMOJI,
        platform_hint=PLATFORM_HINT,
        install_hint="httpx is required; configure MAX_BOT_TOKEN and MAX_CA_BUNDLE when host CA lacks the MAX trust chain",
    )
