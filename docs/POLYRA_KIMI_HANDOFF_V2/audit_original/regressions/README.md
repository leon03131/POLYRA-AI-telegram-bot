# Диагностические воспроизведения

Эти скрипты предназначены для проверенного снимка и воспроизводят его дефекты. Они не обращаются к настоящим provider APIs/Telegram и не подключаются к production DB. additional_checks использует временную SQLite с foreign_keys=ON и минимальный статический index.html; это НЕ PostgreSQL/React E2E.

Из любой директории, после установки нужных зависимостей проекта в изолированном окружении:

```sh
python reproduce_findings.py /path/to/POLYRA-AI-telegram-bot-master
mkdir -p /tmp/polyra-audit-results
python additional_checks.py /path/to/POLYRA-AI-telegram-bot-master /tmp/polyra-audit-results
python static_audit.py /path/to/POLYRA-AI-telegram-bot-master ../evidence
```

У static_audit второй аргумент — директория с исходным inventory.json. Для другого снимка сначала создайте новый baseline inventory; иначе изменённые хеши ожидаемы. Не запускать эти инструменты вместо полноценного pytest/integration suite.

`reproduced: true`, `status: reproduced` и assertions дефектного результата — подтверждение БАГА. После исправления Kimi должен создать настоящие регрессионные assertions корректного поведения. Не сохраняйте прежние ошибочные ожидания ради зелёного результата.

reproduce_findings.py проверяет Done/finalization, trailing usage, quota race, wrong minute reconcile, empty permission semantics, photo persistence, summary coverage, token budget и empty summary. Он использует production functions и test doubles из исходного test_gemini_pool, без подмены aiogram.

additional_checks.py проверяет /admin 404, fallback на env при отключённом DB credential и исчезновение usage после удаления чата.

static_audit.py разбирает исходники и формирует inventory-linked AST/doc/test metadata. Это средство инвентаризации и статических свидетельств, а не автоматическое доказательство функциональной корректности всех файлов.
