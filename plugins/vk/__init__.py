"""VKontakte plugin for Hermes Agent."""

from .adapter import VkAdapter, register, standalone_send

__all__ = ["VkAdapter", "register", "standalone_send"]
