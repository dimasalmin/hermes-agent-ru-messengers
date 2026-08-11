"""Native VK Community Long Poll adapter for Hermes Agent.

This is an external Hermes plugin boundary.  It uses VK's Community API
directly, so Hermes upgrades do not overwrite the transport or its state.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
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
except ImportError:  # pragma: no cover - standalone plugin tests
    @dataclass
    class _FallbackMessageEvent:
        text: str
        message_type: Any = None
        source: Any = None
        raw_message: Any = None
        message_id: Optional[str] = None
        media_urls: List[str] = field(default_factory=list)
        media_types: List[str] = field(default_factory=list)
        reply_to_message_id: Optional[str] = None
        reply_to_text: Optional[str] = None
        metadata: Dict[str, Any] = field(default_factory=dict)

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

from .common import AccessPolicy, extract_media_tags, split_message

from .client import DEFAULT_API_VERSION, VkApiClient, VkApiError
from .interactive import CallbackStore, build_inline_keyboard, build_keyboard
from .media import DEFAULT_MEDIA_MAX_BYTES, DownloadedMedia, download_attachment
from .models import VkCallback, VkIncomingMessage, parse_long_poll_event
from .rate_limit import VK_MESSAGE_LENGTH, reconnect_delay, with_backoff
from .state import VkStateStore
from .formatting import markdown_to_vk

logger = logging.getLogger(__name__)

PLATFORM_NAME = "vk"
PLATFORM_LABEL = "VKontakte"
PLATFORM_EMOJI = "VK"
PLATFORM_HINT = (
    "You are chatting via VKontakte. Keep a response within VK's 4096-character "
    "message limit. The adapter supports text, native buttons, voice notes, photos and documents."
)
VK_CHAT_PEER_OFFSET = 2_000_000_000


def _is_chat_peer(peer_id: int) -> bool:
    return peer_id > VK_CHAT_PEER_OFFSET


def _config_extra(config: Any) -> dict[str, Any]:
    value = getattr(config, "extra", {}) or {}
    return dict(value) if isinstance(value, Mapping) else {}


def _local_env_values() -> dict[str, str]:
    """Read only the plugin credentials from Hermes' dotenv file.

    Hermes' loader may parse ``~/.hermes/.env`` without exporting every value
    into ``os.environ``.  Plugin enablement runs before adapter construction,
    so this small fallback keeps the external plugin self-contained.
    """

    home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    path = Path(os.environ.get("HERMES_ENV_FILE", home / ".env")).expanduser()
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def _env_value(name: str, default: Any = "") -> Any:
    return os.environ.get(name) or _local_env_values().get(name, default)


def _config_token(config: Any) -> str:
    extra = _config_extra(config)
    return str(
        getattr(config, "token", None)
        or extra.get("token")
        or os.environ.get("VK_GROUP_TOKEN", "")
        or _local_env_values().get("VK_GROUP_TOKEN", "")
    ).strip()


def _config_group_id(config: Any) -> int:
    extra = _config_extra(config)
    return _positive_int(
        extra.get("group_id")
        or os.environ.get("VK_GROUP_ID", "")
        or _local_env_values().get("VK_GROUP_ID", ""),
        0,
    )


def _is_command(text: str) -> bool:
    return bool(text) and text.lstrip().startswith("/")


def _policy_mode(value: Any, default: str = "allowlist") -> str:
    mode = str(value or default).strip().lower()
    return mode if mode in {"allowlist", "pairing", "open", "disabled"} else default


def _random_id() -> int:
    return secrets.randbelow(2_147_483_647)


def _classify(text: str, media_types: List[str]) -> Any:
    if _is_command(text):
        return getattr(MessageType, "COMMAND", getattr(MessageType, "TEXT", None))
    if "voice" in media_types:
        return getattr(MessageType, "VOICE", getattr(MessageType, "TEXT", None))
    if "photo" in media_types:
        return getattr(MessageType, "PHOTO", getattr(MessageType, "TEXT", None))
    if "document" in media_types:
        return getattr(MessageType, "DOCUMENT", getattr(MessageType, "TEXT", None))
    return getattr(MessageType, "TEXT", None)


def _send_result_from_ids(message_ids: Iterable[str]) -> Any:
    ids = [str(item) for item in message_ids if item]
    return SendResult(
        success=True,
        message_id=ids[-1] if ids else None,
        continuation_message_ids=tuple(ids[:-1]),
    )


def _cache_media(media: DownloadedMedia) -> Any:
    """Use Hermes' cache when loaded, with older cache helpers as fallback."""

    try:
        from gateway.platforms.base import cache_media_bytes

        return cache_media_bytes(
            media.data,
            filename=media.filename,
            mime_type=media.mime_type,
            default_kind=media.media_type,
        )
    except (ImportError, AttributeError):
        try:
            from gateway.platforms.base import (
                cache_audio_from_bytes,
                cache_document_from_bytes,
                cache_image_from_bytes,
            )

            if media.media_type == "photo":
                return SimpleNamespace(
                    path=cache_image_from_bytes(media.data, ext=Path(media.filename).suffix or ".jpg"),
                    media_type="photo",
                )
            if media.media_type == "voice":
                return SimpleNamespace(
                    path=cache_audio_from_bytes(media.data, ext=Path(media.filename).suffix or ".ogg"),
                    media_type="voice",
                )
            return SimpleNamespace(
                path=cache_document_from_bytes(media.data, filename=media.filename),
                media_type="document",
            )
        except (ImportError, AttributeError):
            logger.warning("Hermes media cache helpers are unavailable; skipping VK attachment")
            return None


class VkAdapter(BasePlatformAdapter):  # type: ignore[misc]
    """VK Community Long Poll adapter using only public Hermes contracts."""

    SUPPORTS_MESSAGE_EDITING = True
    splits_long_messages = True
    MAX_MESSAGE_LENGTH = VK_MESSAGE_LENGTH

    def __init__(self, config: "PlatformConfig") -> None:
        platform = Platform(PLATFORM_NAME) if Platform is not None else PLATFORM_NAME
        super().__init__(config, platform)  # type: ignore[arg-type]
        self._config = config
        self._extra = _config_extra(config)
        self._token = _config_token(config)
        self._group_id = _config_group_id(config)
        self._api_version = str(
            self._extra.get("api_version")
            or _env_value("VK_API_VERSION", DEFAULT_API_VERSION)
        )
        self._poll_wait = _positive_int(
            self._extra.get("poll_wait", _env_value("VK_POLL_WAIT", "25")), 25
        )
        self._require_mention = _truthy(
            self._extra.get("require_mention", _env_value("VK_REQUIRE_MENTION", "true"))
        )
        self._media_max_bytes = _positive_int(
            self._extra.get(
                "media_max_bytes", _env_value("VK_MEDIA_MAX_BYTES", str(DEFAULT_MEDIA_MAX_BYTES))
            ),
            DEFAULT_MEDIA_MAX_BYTES,
        )
        self._access = AccessPolicy.from_env_and_extra(
            allowed_users_env=_env_value("VK_ALLOWED_USERS"),
            group_allowed_users_env=_env_value("VK_GROUP_ALLOWED_USERS"),
            group_allowed_chats_env=_env_value("VK_GROUP_ALLOWED_CHATS"),
            allow_all_env=_env_value("VK_ALLOW_ALL_USERS"),
            guest_mode_env=_env_value("VK_GUEST_MODE"),
            extra=self._extra,
        )
        self._dm_policy = _policy_mode(
            self._extra.get("dm_policy", _env_value("VK_DM_POLICY", "allowlist"))
        )
        self._group_policy = _policy_mode(
            self._extra.get("group_policy", _env_value("VK_GROUP_POLICY", "allowlist"))
        )
        if self._access.allow_all:
            self._dm_policy = "open"
            self._group_policy = "open"
        self._pairing_ttl = _positive_float(
            self._extra.get("pairing_ttl_seconds", _env_value("VK_PAIRING_TTL_SECONDS", "600")),
            600.0,
        )
        self._poll_lock_ttl = _positive_float(
            self._extra.get(
                "poll_lock_ttl_seconds", _env_value("VK_POLL_LOCK_TTL_SECONDS", "180")
            ),
            180.0,
        )
        home = Path(_env_value("HERMES_HOME", "~/.hermes")).expanduser()
        self._state_path = str(
            self._extra.get("state_path")
            or _env_value("VK_STATE_PATH", home / "vk" / "state.sqlite3")
        )
        self._client: VkApiClient | None = None
        self._state: VkStateStore | None = None
        self._polling_task: asyncio.Task | None = None
        self._long_poll: tuple[str, str, str] | None = None
        self._poll_owner = secrets.token_urlsafe(18)
        self._poll_lock_held = False
        self._last_poll_at: float | None = None
        self._last_error: str | None = None
        self._reconnect_attempt = 0
        self._updates_received = 0
        self._callbacks = CallbackStore(
            ttl_seconds=_positive_float(
                self._extra.get(
                    "callback_ttl_seconds", _env_value("VK_CALLBACK_TTL_SECONDS", 900)
                ),
                900.0,
            )
        )
        self._chat_users: dict[str, str] = {}
        self._group_screen_name: str | None = None
        self._model_pickers: dict[str, dict[str, Any]] = {}

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not self._token or self._group_id <= 0:
            logger.error("VK_GROUP_TOKEN and positive VK_GROUP_ID are required")
            return False
        acquire = getattr(self, "_acquire_platform_lock", None)
        if callable(acquire):
            if not acquire("vk", self._token, "VK group token"):
                return False
        self._client = VkApiClient(
            self._token,
            self._group_id,
            api_version=self._api_version,
        )
        self._state = VkStateStore(self._state_path)
        try:
            if not self._state.acquire_poll_lock(
                self._poll_owner, ttl_seconds=self._poll_lock_ttl
            ):
                raise VkApiError("VK Long Poll is already owned by another process")
            self._poll_lock_held = True
            saved = self._state.get_long_poll_state() if is_reconnect else None
            if saved:
                self._long_poll = saved
            else:
                await self._initialize_long_poll()
            self._polling_task = asyncio.create_task(self._poll_loop(), name="vk-community-long-poll")
            self._mark_connected()
            logger.info("VK Community Long Poll connected (group_id=%s)", self._group_id)
            return True
        except Exception:
            logger.exception("VK adapter failed to connect")
            await self.disconnect()
            return False

    async def disconnect(self) -> None:
        task = self._polling_task
        self._polling_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._client is not None:
            await self._client.close()
        self._client = None
        if self._state is not None:
            if self._poll_lock_held:
                self._state.release_poll_lock(self._poll_owner)
                self._poll_lock_held = False
            self._state.close()
        self._state = None
        self._long_poll = None
        release = getattr(self, "_release_platform_lock", None)
        if callable(release):
            release()
        self._mark_disconnected()

    async def _initialize_long_poll(self) -> None:
        if self._client is None or self._state is None:
            raise RuntimeError("VK client is not initialized")
        server = await self._client.get_long_poll_server()
        self._long_poll = (
            str(server["server"]),
            str(server["key"]),
            str(server.get("ts") or "0"),
        )
        self._state.set_long_poll_state(*self._long_poll)

    async def _poll_loop(self) -> None:
        attempt = 0
        while True:
            try:
                if self._client is None:
                    return
                if self._state is not None and not self._state.refresh_poll_lock(
                    self._poll_owner, ttl_seconds=self._poll_lock_ttl
                ):
                    logger.error("VK Long Poll lease was lost; stopping this poller")
                    return
                if self._long_poll is None:
                    await self._initialize_long_poll()
                server, key, ts = self._long_poll
                result = await self._client.long_poll_check(server, key, ts, wait=self._poll_wait)
                self._last_poll_at = time.time()
                self._last_error = None
                self._reconnect_attempt = 0
                if result.needs_reinit:
                    await self._initialize_long_poll()
                    continue
                self._long_poll = (server, key, result.ts)
                if self._state is not None:
                    self._state.set_long_poll_state(*self._long_poll)
                self._updates_received += len(result.updates)
                for update in result.updates:
                    await self._dispatch_update(update)
            except asyncio.CancelledError:
                raise
            except VkApiError as exc:
                if exc.code in {400, 403, 404}:
                    try:
                        await self._initialize_long_poll()
                        attempt = 0
                        continue
                    except VkApiError:
                        logger.warning("VK Long Poll reinitialization failed", exc_info=True)
                self._last_error = str(exc)
                wait = reconnect_delay(attempt, cap=15.0)
                self._reconnect_attempt = attempt + 1
                logger.warning(
                    "VK Long Poll temporarily failed; retry_in=%.1fs: %s", wait, exc
                )
                await asyncio.sleep(wait)
                attempt += 1
            except Exception:
                self._last_error = "unexpected poll loop failure"
                wait = reconnect_delay(attempt, cap=15.0)
                self._reconnect_attempt = attempt + 1
                logger.exception("VK Long Poll loop failed; retry_in=%.1fs", wait)
                await asyncio.sleep(wait)
                attempt += 1

    def health_snapshot(self) -> Dict[str, Any]:
        """Return redacted diagnostics suitable for status output and tests."""

        return {
            "connected": bool(getattr(self, "_running", False)),
            "group_id": self._group_id,
            "poll_lock_held": self._poll_lock_held,
            "last_poll_at": self._last_poll_at,
            "last_error": self._last_error,
            "reconnect_attempt": self._reconnect_attempt,
            "updates_received": self._updates_received,
        }

    async def _dispatch_update(self, update: Mapping[str, Any]) -> None:
        event = parse_long_poll_event(update)
        if isinstance(event, VkCallback):
            await self._dispatch_callback(event)
            return
        if not isinstance(event, VkIncomingMessage):
            return
        if event.from_id < 1:
            return
        if self._state is not None and not self._state.claim_message(event.message_id):
            return
        user_id = str(event.from_id)
        chat_id = str(event.peer_id)
        self._chat_users[chat_id] = user_id
        mentioned = self._is_mentioned(event.text)
        if event.is_group:
            if not self._can_group(user_id, chat_id, mentioned=mentioned):
                return
            if self._require_mention and not mentioned and not _is_command(event.text):
                return
        elif not self._can_dm(user_id):
            if self._dm_policy == "pairing" and await self._try_pairing(user_id, chat_id, event.text):
                return
            return
        if _is_command(event.text) and not self._access.can_run_command(
            user_id, event.text, is_group=event.is_group
        ):
            return

        media_urls: list[str] = []
        media_types: list[str] = []
        note: list[str] = []
        for attachment in event.attachments:
            try:
                downloaded = await download_attachment(  # bounded and host-checked
                    dict(attachment), max_bytes=self._media_max_bytes
                )
                if downloaded is None:
                    continue
                cached = _cache_media(downloaded)
                if cached is None:
                    continue
                path = getattr(cached, "path", None) or cached
                media_urls.append(str(path))
                media_types.append(str(getattr(cached, "media_type", downloaded.media_type)))
            except Exception as exc:  # noqa: BLE001
                logger.warning("VK attachment was not cached: %s", exc)
                note.append("[VK attachment could not be loaded]")

        text = event.text
        if note:
            text = f"{text}\n\n" if text else ""
            text += "\n".join(note)
        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_id,
            chat_type="group" if event.is_group else "dm",
            user_id=user_id,
            user_name=user_id,
            message_id=event.message_id,
        )
        normalized = MessageEvent(
            text=text,
            message_type=_classify(event.text, media_types),
            source=source,
            raw_message=event.raw_event,
            message_id=event.message_id,
            reply_to_message_id=event.reply_to_message_id,
            reply_to_text=event.reply_to_text,
            media_urls=media_urls,
            media_types=media_types,
            metadata={
                "vk_user_id": user_id,
                "vk_peer_id": chat_id,
                "vk_group_id": self._group_id,
                "vk_payload": event.payload,
            },
        )
        await self.handle_message(normalized)

    def _can_dm(self, user_id: str) -> bool:
        if self._dm_policy == "disabled":
            return False
        if self._dm_policy == "open":
            return True
        if self._dm_policy == "pairing":
            return bool(self._state and self._state.is_paired(user_id)) or self._access.can_dm(user_id)
        return self._access.can_dm(user_id)

    def _can_group(self, user_id: str, chat_id: str, *, mentioned: bool) -> bool:
        if self._group_policy == "disabled":
            return False
        if self._group_policy == "open":
            return True
        return self._access.can_group(user_id, chat_id, mentioned=mentioned)

    async def _try_pairing(self, user_id: str, peer_id: str, text: str) -> bool:
        parts = text.strip().split(maxsplit=1)
        if len(parts) != 2 or parts[0].lower() not in {"/pair", "/pairing"}:
            return False
        if self._state is None or not self._state.approve_pairing(user_id, parts[1].strip()):
            return False
        await self.send(
            peer_id,
            "Подключение подтверждено. Теперь можно отправлять запросы Hermes Agent.",
        )
        return True

    def _is_mentioned(self, text: str) -> bool:
        lowered = text.lower()
        if self._group_screen_name and f"@{self._group_screen_name.lower()}" in lowered:
            return True
        return bool(self._group_id and f"[club{self._group_id}|" in lowered)

    async def _dispatch_callback(self, callback: VkCallback) -> None:
        if self._client is None:
            return
        is_group = _is_chat_peer(callback.peer_id)
        authorized = (
            self._can_group(callback.user_id, str(callback.peer_id), mentioned=False)
            if is_group
            else self._can_dm(callback.user_id)
        )
        if not authorized:
            await self._answer_callback(callback, "Нет доступа к этой кнопке.")
            return
        entry = self._callbacks.consume_for_context(
            str(callback.payload), user_id=callback.user_id, peer_id=str(callback.peer_id)
        )
        if entry is None:
            await self._answer_callback(callback, "Кнопка устарела или уже использована.")
            return
        kind, action, session_key = entry["kind"], entry["action"], entry["session_key"]
        try:
            if kind == "approval":
                from tools.approval import resolve_gateway_approval

                count = resolve_gateway_approval(session_key, action)
                labels = {
                    "once": "Разрешено один раз",
                    "session": "Разрешено на сессию",
                    "always": "Разрешено всегда",
                    "deny": "Запрещено",
                }
                await self._answer_callback(callback, labels.get(action, "Запрос обработан") if count else "Запрос уже завершён")
                return
            if kind == "slash":
                from tools import slash_confirm

                choice, confirm_id = action.split(":", 1)
                result = await slash_confirm.resolve(session_key, confirm_id, choice)
                await self._answer_callback(callback, {"once": "Подтверждено", "always": "Подтверждено всегда", "cancel": "Отменено"}.get(choice, "Обработано"))
                if result:
                    await self.send(str(callback.peer_id), str(result))
                return
            if kind == "clarify":
                clarify_id, choice = action.split(":", 1)
                if choice == "other":
                    from tools.clarify_gateway import mark_awaiting_text

                    ok = mark_awaiting_text(clarify_id)
                    await self._answer_callback(callback, "Введите свой вариант следующим сообщением." if ok else "Запрос уже завершён.")
                    return
                from tools.clarify_gateway import _entries as clarify_entries, resolve_gateway_clarify

                resolved_text = f"choice {int(choice) + 1}"
                clarify_entry = clarify_entries.get(clarify_id)
                if clarify_entry and clarify_entry.choices and 0 <= int(choice) < len(clarify_entry.choices):
                    resolved_text = str(clarify_entry.choices[int(choice)])
                ok = resolve_gateway_clarify(clarify_id, resolved_text)
                await self._answer_callback(callback, f"Выбрано: {resolved_text}" if ok else "Запрос уже завершён.")
                return
            if kind == "model":
                await self._dispatch_model_callback(callback, action)
                return
            await self._answer_callback(callback, "Неизвестная кнопка.")
        except Exception:
            logger.exception("VK callback resolution failed")
            await self._answer_callback(callback, "Не удалось обработать кнопку.")

    async def _dispatch_model_callback(self, callback: VkCallback, action: str) -> None:
        parts = str(action).split(":", 2)
        if len(parts) < 2:
            await self._answer_callback(callback, "Некорректная кнопка выбора модели.")
            return
        picker_id, operation = parts[0], parts[1]
        state = self._model_pickers.get(picker_id)
        if state is None:
            await self._answer_callback(callback, "Выбор модели устарел. Откройте /model заново.")
            return
        if operation == "cancel":
            self._model_pickers.pop(picker_id, None)
            await self._answer_callback(callback, "Выбор модели отменен.")
            return
        if operation == "provider" and len(parts) == 3:
            provider = parts[2]
            models = list(state.get("models", {}).get(provider, []))
            if not models:
                await self._answer_callback(callback, "У провайдера нет доступных моделей.")
                return
            rows = []
            for index, model in enumerate(models[:50]):
                payload = self._callbacks.issue(
                    "model",
                    f"{picker_id}:model:{index}",
                    user_id=callback.user_id,
                    peer_id=str(callback.peer_id),
                    session_key=str(state["session_key"]),
                )
                rows.append([{"label": str(model).rsplit("/", 1)[-1][:40], "payload": payload, "color": "primary"}])
            state["selected_provider"] = provider
            await self._answer_callback(callback, f"Провайдер: {provider}. Выберите модель.")
            await self._send_interactive(str(callback.peer_id), f"Провайдер: {provider}\n\nВыберите модель:", rows)
            return
        if operation == "model" and len(parts) == 3:
            try:
                index = int(parts[2])
                models = list(state.get("models", {}).get(str(state.get("selected_provider")), []))
                model_id = str(models[index])
            except (ValueError, IndexError, TypeError):
                await self._answer_callback(callback, "Модель не найдена.")
                return
            callback_fn = state.get("on_model_selected")
            if not callable(callback_fn):
                self._model_pickers.pop(picker_id, None)
                await self._answer_callback(callback, "Обработчик выбора модели недоступен.")
                return
            result = callback_fn(str(callback.peer_id), model_id, str(state.get("selected_provider") or ""))
            if inspect.isawaitable(result):
                result = await result
            self._model_pickers.pop(picker_id, None)
            await self._answer_callback(callback, str(result or f"Модель переключена: {model_id}"))
            return
        await self._answer_callback(callback, "Неизвестное действие выбора модели.")

    async def _answer_callback(self, callback: VkCallback, text: str) -> None:
        if self._client is None:
            return
        try:
            await self._client.answer_message_event(callback.event_id, int(callback.user_id))
            await self.send(str(callback.peer_id), text)
        except VkApiError:
            logger.warning("VK callback answer failed", exc_info=True)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if self._client is None:
            return SendResult(success=False, error="VK adapter is not connected", retryable=True)
        media_paths, text = extract_media_tags(content)
        attachments: list[str] = []
        ids: list[str] = []
        try:
            for path in media_paths:
                attachments.append(await self._upload(int(chat_id), path))
            chunks = split_message(text, VK_MESSAGE_LENGTH) if text else ([""] if attachments else [])
            for index, chunk in enumerate(chunks):
                rendered, format_data = markdown_to_vk(chunk)

                async def _send() -> int:
                    return await self._client.send_message(
                        int(chat_id),
                        rendered,
                        random_id=_random_id(),
                        reply_to=int(reply_to) if index == 0 and reply_to and str(reply_to).isdigit() else None,
                        attachment=",".join(attachments) if index == 0 and attachments else None,
                        format_data=format_data,
                    )

                message_id = await with_backoff(_send)
                ids.append(str(message_id))
            return _send_result_from_ids(ids)
        except VkApiError as exc:
            if media_paths and not ids:
                fallback = text or "Не удалось отправить вложение."
                return await self.send(chat_id, fallback, reply_to=reply_to, metadata=metadata)
            return SendResult(
                success=False,
                error=str(exc),
                retryable=exc.retryable,
                retry_after=exc.retry_after,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            if media_paths and not ids:
                fallback = text or "Не удалось отправить вложение."
                return await self.send(chat_id, fallback, reply_to=reply_to, metadata=metadata)
            return SendResult(success=False, error=str(exc))

    async def _upload(self, peer_id: int, path: str) -> str:
        if self._client is None:
            raise VkApiError("VK adapter is not connected", retryable=True)
        suffix = Path(path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            return await self._client.upload_photo(peer_id, path, max_bytes=self._media_max_bytes)
        if suffix in {".ogg", ".oga", ".opus", ".mp3", ".wav", ".m4a"}:
            return await self._client.upload_audio(peer_id, path, max_bytes=self._media_max_bytes)
        return await self._client.upload_document(peer_id, path, max_bytes=self._media_max_bytes)

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> Any:
        del finalize
        if self._client is None:
            return SendResult(success=False, error="VK adapter is not connected", retryable=True)
        _media, text = extract_media_tags(content)
        rendered, format_data = markdown_to_vk(text[:VK_MESSAGE_LENGTH])
        try:
            await self._client.edit_message(
                int(chat_id),
                int(message_id),
                rendered,
                format_data=format_data,
            )
            return SendResult(success=True, message_id=str(message_id))
        except VkApiError as exc:
            return SendResult(success=False, error=str(exc), retryable=exc.retryable)

    async def send_typing(self, chat_id: str, metadata: Any = None) -> None:
        del metadata
        if self._client is not None:
            try:
                await self._client.set_typing(int(chat_id))
            except VkApiError:
                logger.debug("VK typing indicator failed", exc_info=True)

    async def send_keyboard(
        self,
        chat_id: str,
        content: str,
        rows: list[list[Mapping[str, Any]]],
        *,
        inline: bool = True,
        reply_to: Optional[str] = None,
    ) -> Any:
        if self._client is None:
            return SendResult(success=False, error="VK adapter is not connected", retryable=True)
        keyboard = build_keyboard(rows, inline=inline)
        rendered, format_data = markdown_to_vk(content)
        try:
            message_id = await with_backoff(
                lambda: self._client.send_message(
                    int(chat_id),
                    rendered,
                    random_id=_random_id(),
                    reply_to=int(reply_to) if reply_to and str(reply_to).isdigit() else None,
                    keyboard=keyboard,
                    format_data=format_data,
                )
            )
            return SendResult(success=True, message_id=str(message_id))
        except VkApiError as exc:
            return SendResult(success=False, error=str(exc), retryable=exc.retryable)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": str(chat_id), "type": "group" if _is_chat_peer(int(chat_id)) else "dm", "chat_id": str(chat_id)}

    def _interactive_context(self, chat_id: str, metadata: Optional[Mapping[str, Any]]) -> tuple[str, str] | None:
        user_id = str((metadata or {}).get("vk_user_id") or self._chat_users.get(str(chat_id)) or "")
        if not user_id:
            return None
        return str(chat_id), user_id

    async def _send_interactive(self, chat_id: str, text: str, rows: list[list[Mapping[str, Any]]]) -> Any:
        if self._client is None:
            return SendResult(success=False, error="VK adapter is not connected", retryable=True)
        try:
            keyboard = build_inline_keyboard(rows)
        except ValueError as exc:
            logger.warning("VK interactive keyboard exceeds native limits: %s", exc)
            return await self.send(chat_id, text)
        rendered, format_data = markdown_to_vk(text[:VK_MESSAGE_LENGTH])
        try:
            message_id = await self._client.send_message(
                int(chat_id),
                rendered,
                random_id=_random_id(),
                keyboard=keyboard,
                format_data=format_data,
            )
            return SendResult(success=True, message_id=str(message_id))
        except VkApiError as exc:
            return SendResult(success=False, error=str(exc), retryable=exc.retryable)

    async def send_clarify(self, chat_id: str, question: str, choices: Optional[list], clarify_id: str, session_key: str, metadata: Optional[Dict[str, Any]] = None) -> Any:
        context = self._interactive_context(chat_id, metadata)
        if context is None or not choices:
            return await self.send(chat_id, f"❓ {question}", metadata=metadata)
        peer_id, user_id = context
        rows = [[{"label": str(choice)[:40], "payload": self._callbacks.issue("clarify", f"{clarify_id}:{index}", user_id=user_id, peer_id=peer_id, session_key=session_key), "color": "primary"}] for index, choice in enumerate(choices[:50])]
        rows.append([{"label": "Другое", "payload": self._callbacks.issue("clarify", f"{clarify_id}:other", user_id=user_id, peer_id=peer_id, session_key=session_key), "color": "secondary"}])
        return await self._send_interactive(chat_id, f"❓ {question}\n\n" + "\n".join(f"{i + 1}. {choice}" for i, choice in enumerate(choices[:50])), rows)

    async def send_exec_approval(self, chat_id: str, command: str, session_key: str, description: str = "dangerous command", metadata: Optional[Dict[str, Any]] = None, allow_permanent: bool = True, allow_session: bool = True, smart_denied: bool = False) -> Any:
        del smart_denied
        context = self._interactive_context(chat_id, metadata)
        if context is None:
            return SendResult(success=False, error="VK native buttons require a known user context")
        peer_id, user_id = context
        choices = [("once", "Разрешить один раз")]
        if allow_session:
            choices.append(("session", "Разрешить на сессию"))
        if allow_permanent:
            choices.append(("always", "Разрешить всегда"))
        choices.append(("deny", "Запретить"))
        rows = [[{"label": label, "payload": self._callbacks.issue("approval", action, user_id=user_id, peer_id=peer_id, session_key=session_key), "color": "positive" if action != "deny" else "negative"} for action, label in choices[index:index + 2]] for index in range(0, len(choices), 2)]
        preview = command[:3000] + ("..." if len(command) > 3000 else "")
        return await self._send_interactive(chat_id, f"⚠ Требуется подтверждение команды:\n\n```\n{preview}\n```\nПричина: {description}", rows)

    async def send_slash_confirm(self, chat_id: str, title: str, message: str, session_key: str, confirm_id: str, metadata: Optional[Dict[str, Any]] = None) -> Any:
        context = self._interactive_context(chat_id, metadata)
        if context is None:
            return SendResult(success=False, error="VK native buttons require a known user context")
        peer_id, user_id = context
        choices = [("once", "Подтвердить один раз"), ("always", "Подтверждать всегда"), ("cancel", "Отмена")]
        rows = [[{"label": label, "payload": self._callbacks.issue("slash", f"{action}:{confirm_id}", user_id=user_id, peer_id=peer_id, session_key=session_key), "color": "negative" if action == "cancel" else "primary"}] for action, label in choices]
        return await self._send_interactive(chat_id, f"{title}\n\n{message}", rows)

    async def send_model_picker(self, chat_id: str, providers: list, current_model: str, current_provider: str, session_key: str, on_model_selected: Any, metadata: Optional[Dict[str, Any]] = None) -> Any:
        context = self._interactive_context(chat_id, metadata)
        if context is None:
            return SendResult(success=False, error="VK native buttons require a known user context")
        peer_id, user_id = context
        picker_id = secrets.token_urlsafe(8)
        self._model_pickers[picker_id] = {"providers": providers, "models": {}, "on_model_selected": on_model_selected, "session_key": session_key, "user_id": user_id, "peer_id": peer_id, "current_model": current_model, "current_provider": current_provider}
        rows = []
        for provider in [item for item in providers if isinstance(item, Mapping)][:20]:
            slug = str(provider.get("slug") or "")
            if not slug:
                continue
            self._model_pickers[picker_id]["models"][slug] = [str(item) for item in provider.get("models", [])]
            payload = self._callbacks.issue("model", f"{picker_id}:provider:{slug}", user_id=user_id, peer_id=peer_id, session_key=session_key)
            rows.append([{"label": str(provider.get("name") or slug)[:40], "payload": payload, "color": "primary"}])
        return await self._send_interactive(chat_id, f"Текущая модель: {current_model}\nПровайдер: {current_provider}\n\nВыберите провайдера:", rows)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(value: Any, default: int) -> int:
    try:
        result = int(value)
        return result if result > 0 else default
    except (TypeError, ValueError):
        return default


def _positive_float(value: Any, default: float) -> float:
    try:
        result = float(value)
        return result if result > 0 else default
    except (TypeError, ValueError):
        return default


def check_requirements() -> bool:
    try:
        import httpx  # noqa: F401
    except ImportError:
        return False
    return True


def validate_config(config: "PlatformConfig") -> bool:
    return bool(_config_token(config) and _config_group_id(config) > 0)


def is_connected(config: "PlatformConfig") -> bool:
    return validate_config(config)


def env_enablement() -> Optional[Dict[str, Any]]:
    values = _local_env_values()
    token = (os.environ.get("VK_GROUP_TOKEN") or values.get("VK_GROUP_TOKEN", "")).strip()
    group_id = (os.environ.get("VK_GROUP_ID") or values.get("VK_GROUP_ID", "")).strip()
    if not token or not group_id:
        return None
    result: Dict[str, Any] = {"enabled": True, "token": token, "group_id": group_id}
    home_channel = os.environ.get("VK_HOME_CHANNEL", "").strip()
    if home_channel:
        result["home_channel"] = home_channel
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
    del thread_id, force_document
    token = _config_token(pconfig)
    extra = _config_extra(pconfig)
    group_id = _config_group_id(pconfig)
    if not token or not group_id:
        return {"error": "VK_GROUP_TOKEN and VK_GROUP_ID are required"}
    client = VkApiClient(token, group_id, api_version=str(extra.get("api_version", DEFAULT_API_VERSION)))
    try:
        attachments = []
        for path in media_files or []:
            suffix = Path(path).suffix.lower()
            if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                attachments.append(await client.upload_photo(int(chat_id), path, max_bytes=DEFAULT_MEDIA_MAX_BYTES))
            elif suffix in {".ogg", ".oga", ".opus", ".mp3", ".wav", ".m4a"}:
                attachments.append(await client.upload_audio(int(chat_id), path, max_bytes=DEFAULT_MEDIA_MAX_BYTES))
            else:
                attachments.append(await client.upload_document(int(chat_id), path, max_bytes=DEFAULT_MEDIA_MAX_BYTES))
        ids = []
        chunks = split_message(message, VK_MESSAGE_LENGTH) if message else ([""] if attachments else [])
        for index, chunk in enumerate(chunks):
            ids.append(str(await client.send_message(int(chat_id), chunk, random_id=_random_id(), attachment=",".join(attachments) if index == 0 else None)))
        return {"message_id": ids[-1] if ids else None, "message_ids": ids}
    except VkApiError as exc:
        return {"error": str(exc), "retryable": exc.retryable}
    finally:
        await client.close()


def register(ctx: Any) -> None:
    ctx.register_platform(
        name=PLATFORM_NAME,
        label=PLATFORM_LABEL,
        adapter_factory=lambda cfg: VkAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        env_enablement_fn=env_enablement,
        cron_deliver_env_var="VK_HOME_CHANNEL",
        allowed_users_env="VK_ALLOWED_USERS",
        allow_all_env="VK_ALLOW_ALL_USERS",
        max_message_length=VK_MESSAGE_LENGTH,
        platform_hint=PLATFORM_HINT,
        emoji=PLATFORM_EMOJI,
        install_hint="httpx is required; configure VK_GROUP_TOKEN and VK_GROUP_ID",
        standalone_sender_fn=standalone_send,
    )
    register_cli = getattr(ctx, "register_cli_command", None)
    if callable(register_cli):
        from .cli import handle_cli, setup_cli

        register_cli(
            name="vk",
            help="VK channel administration",
            setup_fn=setup_cli,
            handler_fn=handle_cli,
            description="Inspect VK state and manage opt-in DM pairing.",
        )
