"""Минимальная валидация JSON Schema без внешних зависимостей + адаптер в LLMTool.

Поддерживается ровно тот поднабор, что используется в схемах наших инструментов:
type (object/array/string/number/integer/boolean), required, properties,
additionalProperties (bool), enum, items (array), minimum/maximum (и алиасы
min/max) для чисел, minLength/maxLength для строк. Неизвестные ключи схемы
игнорируются.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.llm.base import LLMTool

if TYPE_CHECKING:
    from app.llm.tools.registry import ToolDefinition


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_TYPE_CHECKS: dict[str, Any] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": _is_number,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def validate_json_schema(schema: dict[str, Any], instance: dict[str, Any]) -> list[str]:
    """Проверить instance по JSON Schema; вернуть список ошибок ([] = валидно)."""
    errors: list[str] = []
    _validate(schema, instance, "", errors)
    return errors


def _validate(schema: Any, value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict):
        return
    if not _check_type(schema, value, path, errors):
        return  # дальнейшие проверки бессмысленны при неверном типе
    _check_enum(schema, value, path, errors)
    if isinstance(value, dict):
        _validate_object(schema, value, path, errors)
    elif isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate(items, item, f"{path or '<root>'}[{index}]", errors)
    elif isinstance(value, str):
        _check_string(schema, value, path, errors)
    elif _is_number(value):
        _check_number(schema, value, path, errors)


def _check_type(schema: dict[str, Any], value: Any, path: str, errors: list[str]) -> bool:
    """Проверка type. False — тип неверный (ошибка записана), дальше не проверять."""
    expected = schema.get("type")
    where = path or "<root>"
    if isinstance(expected, str):
        check = _TYPE_CHECKS.get(expected)
        if check is not None and not check(value):
            errors.append(f"{where}: expected {expected}")
            return False
    elif isinstance(expected, list):
        if not any((c := _TYPE_CHECKS.get(t)) is not None and c(value) for t in expected):
            errors.append(f"{where}: expected {' | '.join(str(t) for t in expected)}")
            return False
    return True


def _check_enum(schema: dict[str, Any], value: Any, path: str, errors: list[str]) -> None:
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path or '<root>'}: value {value!r} not in enum {enum!r}")


def _check_string(schema: dict[str, Any], value: str, path: str, errors: list[str]) -> None:
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and len(value) < min_length:
        errors.append(f"{path or '<root>'}: shorter than minLength {min_length}")
    max_length = schema.get("maxLength")
    if isinstance(max_length, int) and len(value) > max_length:
        errors.append(f"{path or '<root>'}: longer than maxLength {max_length}")


def _check_number(schema: dict[str, Any], value: Any, path: str, errors: list[str]) -> None:
    minimum = schema.get("minimum", schema.get("min"))
    if _is_number(minimum) and value < minimum:
        errors.append(f"{path or '<root>'}: {value} < minimum {minimum}")
    maximum = schema.get("maximum", schema.get("max"))
    if _is_number(maximum) and value > maximum:
        errors.append(f"{path or '<root>'}: {value} > maximum {maximum}")


def _validate_object(
    schema: dict[str, Any], value: dict[str, Any], path: str, errors: list[str]
) -> None:
    where = path or "<root>"
    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if key not in value:
                errors.append(f"{where}: missing required property '{key}'")
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, subschema in properties.items():
            if key in value:
                child_path = f"{path}.{key}" if path else key
                _validate(subschema, value[key], child_path, errors)
    if schema.get("additionalProperties") is False and isinstance(properties, dict):
        for key in value:
            if key not in properties:
                errors.append(f"{where}: unexpected property '{key}'")


def make_llm_tools(definitions: list[ToolDefinition]) -> list[LLMTool]:
    """ToolDefinition (tool engine) → LLMTool (провайдер-нейтральный формат)."""
    return [
        LLMTool(
            name=definition.name,
            description=definition.description,
            parameters=definition.parameters,
        )
        for definition in definitions
    ]
