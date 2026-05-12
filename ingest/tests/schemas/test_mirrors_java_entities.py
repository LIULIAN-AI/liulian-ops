from __future__ import annotations

import re
from typing import Any, get_args, get_origin
from types import NoneType
from uuid import UUID

import crawler.schemas as schemas


def _is_optional(annotation: Any) -> bool:
    return NoneType in get_args(annotation) or get_origin(annotation) is NoneType


def _field_max_length(field: Any) -> int | None:
    for metadata in field.metadata:
        max_length = getattr(metadata, "max_length", None)
        if max_length is not None:
            return int(max_length)
    return None


def _camel_to_snake(name: str) -> str:
    if "_" in name:
        return "_".join(_camel_to_snake(part) if part else part for part in name.split("_"))
    name = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return name.lower()


def _rows_by_class(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["class_name"]), []).append(row)
    return grouped


def test_schema_registry_exports_classes() -> None:
    missing = [name for name in schemas.__all__ if not hasattr(schemas, name)]
    assert missing == []


def test_models_mirror_java_columns(java_columns: list[dict[str, Any]]) -> None:
    rows_by_class = _rows_by_class(java_columns)
    errors: list[str] = []
    for class_name in schemas.__all__:
        if class_name == "FieldProvenance":
            continue
        model = getattr(schemas, class_name)
        model_fields = model.model_fields
        java_fields = {str(row["pydantic_name"]): row for row in rows_by_class.get(class_name, [])}
        missing = sorted(set(java_fields) - set(model_fields))
        extra = sorted(set(model_fields) - set(java_fields))
        if missing:
            errors.append(f"{class_name}: missing Pydantic fields {missing}")
        if extra:
            errors.append(f"{class_name}: extra Pydantic fields {extra}")
        for field_name, row in java_fields.items():
            if field_name not in model_fields:
                continue
            field = model_fields[field_name]
            expected_length = None
            if row.get("length") is not None and "TEXT" not in str(row.get("definition", "")).upper():
                expected_length = int(row["length"])
            actual_length = _field_max_length(field)
            if actual_length != expected_length:
                errors.append(
                    f"{class_name}.{field_name}: max_length {actual_length} != {expected_length}"
                )
            if row.get("primary_key"):
                if not field.is_required():
                    errors.append(f"{class_name}.{field_name}: primary key must be required")
                continue
            expected_optional = bool(row["nullable"])
            actual_optional = _is_optional(field.annotation)
            if actual_optional != expected_optional:
                errors.append(
                    f"{class_name}.{field_name}: optional {actual_optional} != {expected_optional}"
                )
            if not expected_optional and not field.is_required():
                errors.append(f"{class_name}.{field_name}: non-nullable field must be required")
    assert errors == []


def test_models_use_forbid_and_camel_aliases() -> None:
    for class_name in schemas.__all__:
        model = getattr(schemas, class_name)
        assert model.model_config["extra"] == "forbid"
    assert "companySortId" in schemas.Company.model_json_schema(by_alias=True)["properties"]


def test_join_columns_use_fk_names_without_duplicate_columns(
    java_columns: list[dict[str, Any]],
) -> None:
    errors: list[str] = []
    columns_by_class: dict[str, set[str]] = {}
    for row in java_columns:
        class_columns = columns_by_class.setdefault(str(row["class_name"]), set())
        column_key = str(row["column"])
        if column_key in class_columns:
            errors.append(f"{row['class_name']}: duplicate column {row['column']}")
        class_columns.add(column_key)
        if row.get("is_join_column") and str(row["column"]).endswith("_id"):
            expected_name = _camel_to_snake(str(row["column"]))
            if row["pydantic_name"] != expected_name:
                errors.append(
                    f"{row['class_name']}.{row['java_field']}: "
                    f"join pydantic_name {row['pydantic_name']} != {expected_name}"
                )
    assert errors == []


def test_list_string_fields_keep_list_semantics() -> None:
    tag = schemas.CompanyNews.model_fields["tag"]
    annotation = tag.annotation
    if _is_optional(annotation):
        annotation = next(item for item in get_args(annotation) if item is not NoneType)
    assert get_origin(annotation) is list
    assert get_args(annotation) == (str,)


def test_join_columns_follow_target_primary_key_type() -> None:
    campaign_id = schemas.CampaignUrl.model_fields["campaign_id"].annotation
    if _is_optional(campaign_id):
        campaign_id = next(item for item in get_args(campaign_id) if item is not NoneType)
    assert campaign_id is UUID

    location_id = schemas.CompanyLocation.model_fields["location_id"].annotation
    if _is_optional(location_id):
        location_id = next(item for item in get_args(location_id) if item is not NoneType)
    assert location_id is UUID
