from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import Field, create_model

from crawler.schemas._base import _Base

SCHEMA_DIR = Path(__file__).resolve().parent
JAVA_COLUMNS_PATH = SCHEMA_DIR / "_java_columns.json"


def _java_columns() -> list[dict[str, Any]]:
    with JAVA_COLUMNS_PATH.open(encoding="utf-8") as file:
        rows = json.load(file)
    if not isinstance(rows, list):
        raise ValueError(f"{JAVA_COLUMNS_PATH} must contain a JSON array")
    return [row for row in rows if isinstance(row, dict)]


def _python_type(java_type: str, nullable: bool, enum_values: list[str] | None) -> Any:
    normalized = java_type.removeprefix("java.lang.").removeprefix("java.util.")
    base: Any
    if enum_values is not None:
        base = Literal.__getitem__(tuple(enum_values))
    elif normalized == "List<String>":
        base = list[str]
    elif normalized == "String":
        base = str
    elif normalized in {"Long", "long", "Integer", "int", "BigInt"}:
        base = int
    elif normalized in {"Boolean", "boolean"}:
        base = bool
    elif normalized in {"Double", "double", "Float", "float"}:
        base = float
    elif normalized in {"LocalDateTime", "Date", "LocalDate"}:
        base = datetime if normalized == "LocalDateTime" else date
    elif normalized in {"UUID", "java.util.UUID"}:
        base = UUID
    else:
        base = str
    if nullable:
        return base | None
    return base


def _field_definition(row: dict[str, Any]) -> tuple[Any, Any]:
    nullable = bool(row["nullable"]) and not bool(row.get("primary_key"))
    raw_enum_values = row.get("enum_values")
    enum_values = raw_enum_values if isinstance(raw_enum_values, list) else None
    annotation = _python_type(str(row["java_type"]), nullable, enum_values)
    default = None if nullable else ...
    kwargs: dict[str, Any] = {}
    length = row.get("length")
    definition = str(row.get("definition") or "").upper()
    if length is not None and "TEXT" not in definition:
        kwargs["max_length"] = int(length)
    if row.get("primary_key"):
        kwargs["description"] = "primary key"
    return annotation, Field(default, **kwargs)


def build_models(module_name: str, class_names: tuple[str, ...]) -> dict[str, type[_Base]]:
    rows = _java_columns()
    models: dict[str, type[_Base]] = {}
    for class_name in class_names:
        fields = {
            str(row["pydantic_name"]): _field_definition(row)
            for row in rows
            if row.get("schema_module") == module_name and row.get("class_name") == class_name
        }
        model = create_model(class_name, __base__=_Base, **fields)  # type: ignore[call-overload]
        models[class_name] = cast(type[_Base], model)
    return models
