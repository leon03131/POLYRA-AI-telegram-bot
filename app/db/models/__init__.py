"""ORM-модели. Импорт этого модуля регистрирует все модели в Base.metadata."""

from app.db.models.access import AccessGrant, UserModelPermission, UserSettings
from app.db.models.audit_log import AuditLog
from app.db.models.chat import Chat
from app.db.models.chat_summary import ChatSummary
from app.db.models.credential import ProviderCredential
from app.db.models.gemini import GeminiProject, QuotaDailyUsage, QuotaMinuteUsage, QuotaPolicy
from app.db.models.generation_run import GenerationRun
from app.db.models.memory import Memory
from app.db.models.message import Message, MessagePart
from app.db.models.search_config import SearchBackendConfig
from app.db.models.system_setting import SystemSetting
from app.db.models.tool_call import ToolCallRecord
from app.db.models.user import User

__all__ = [
    "AccessGrant",
    "AuditLog",
    "Chat",
    "ChatSummary",
    "GeminiProject",
    "GenerationRun",
    "Memory",
    "Message",
    "MessagePart",
    "ProviderCredential",
    "QuotaDailyUsage",
    "QuotaMinuteUsage",
    "QuotaPolicy",
    "SearchBackendConfig",
    "SystemSetting",
    "ToolCallRecord",
    "User",
    "UserModelPermission",
    "UserSettings",
]
