"""Telegram message drafts streaming (Bot API 10.x)."""

from app.bot.streaming.draft import DraftStreamer, new_draft_id

__all__ = ["DraftStreamer", "new_draft_id"]
