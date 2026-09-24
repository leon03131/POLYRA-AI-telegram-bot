## POLYRA FIX V2 — активное задание исправления

Основной пакет: `docs/_handoff/POLYRA_KIMI_HANDOFF_V2/` (уточнить фактический путь,
если владелец распаковал иначе). Перед работой читать REQUIREMENTS_ADDENDUM.md
и KIMI_FIX_PROMPT.txt из корня пакета, а не старый prompt в audit_original/.

Обязательное реальное делегирование через OpenCode; максимум четыре активных
субагента, без вложенного размножения. Scopes и реальные вызовы фиксируются в
`.agents/POLYRA_FIX_V2_DISPATCH.md`; один writer на файл, lead интегрирует.
Профили polyra-* имеют специализации; их наличие не равно фактическому запуску.

Существующие модели сохраняются. Ошибки timeout/429/5xx не означают unsupported.
Добавить `deepseek-v4-pro` отдельно от Flash через существующий AlibabaProvider,
не менять endpoints и модель разработки Kimi. Pro не получает image capability
и LOW-семантику от другой модели без подтверждения.

A01–A40 остаются историческими findings для проверки на текущем checkout.
N01–N04 — новые обязательные требования. Старый source snapshot не распаковывать
поверх актуального кода. Не терять данные, secrets, permissions и пользовательские edits.
Сдача: FIX_REPORT_V2.md с реальными tests/exit codes, model matrix и открытыми blockers.
