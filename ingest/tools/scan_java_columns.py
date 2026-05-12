#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND_MODEL_SUFFIX = Path("src/main/java/com/neobanker/neobank/models")


def _backend_models_root() -> Path:
    candidates = (
        ROOT.parent / "neobanker-backend-MVP-V2" / BACKEND_MODEL_SUFFIX,
        ROOT.parent / "backend" / BACKEND_MODEL_SUFFIX,
        ROOT.parent / "neobanker-backend" / BACKEND_MODEL_SUFFIX,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


BACKEND_MODELS = _backend_models_root()
OUTPUT_PATH = ROOT / "tools" / "_java_columns.json"
PACKAGE_OUTPUT_PATH = ROOT / "src" / "crawler" / "schemas" / "_java_columns.json"
ENUMS_PATH = BACKEND_MODELS.parent / "enums"

IN_SCOPE: dict[str, str] = {
    "company/Company.java": "company",
    "company/Finance.java": "company",
    "company/CompanyTag.java": "company",
    "company/CompanyNews.java": "company",
    "company/CompanyNewsTag.java": "company",
    "company/Director.java": "company",
    "company/Funding.java": "company",
    "company/Investment.java": "company",
    "company/License.java": "company",
    "company/LicenseObtainment.java": "company",
    "company/Location.java": "company",
    "company/CompanyLocation.java": "company",
    "company/CompanyOwner.java": "company",
    "company/IOSApp.java": "company",
    "company/Marketing.java": "company",
    "company/Web3.java": "company",
    "company/Shareholder.java": "company",
    "company/ShareholderTag.java": "company",
    "company/ManagementTeamStaff.java": "company",
    "company/ManagementTeamTag.java": "company",
    "company/Report.java": "company",
    "company/Campaign.java": "company",
    "company/CampaignUrl.java": "company",
    "compliance/Initiative.java": "compliance",
    "compliance/Regulatory.java": "compliance",
    "financials/Financials.java": "financials",
    "financials/FFunding.java": "financials",
    "financials/FInvestment.java": "financials",
    "financials/FinancialData.java": "financials",
    "financials/Bank.java": "financials",
    "financials/InvestorInfo.java": "financials",
    "marketing/MarketingAppLatestRecord.java": "marketing",
    "marketing/MarketingBaseHeader.java": "marketing",
    "marketing/MarketingMediaInfo.java": "marketing",
    "marketing/MarketingSocialMediaDetail.java": "marketing",
    "product/BankAccount.java": "product",
    "product/Card.java": "product",
    "product/CardWelfare.java": "product",
    "product/CompanyProduct.java": "product",
    "product/Deposit.java": "product",
    "product/InterestRate.java": "product",
    "product/Lending.java": "product",
    "product/Partner.java": "product",
    "product/TransferAndExchange.java": "product",
    "staff/StaffEmployeeNoAndTech.java": "staff",
    "staff/StaffManagement.java": "staff",
    "staff/StaffShareholder.java": "staff",
}

SIMPLE_TYPES = {
    "String",
    "Long",
    "long",
    "Integer",
    "int",
    "Boolean",
    "boolean",
    "Double",
    "double",
    "Float",
    "float",
    "BigInt",
    "LocalDateTime",
    "LocalDate",
    "Date",
    "UUID",
    "List<String>",
}

ENUM_TYPES = {
    "BackgroundType",
    "CampaignType",
    "ClientType",
    "CompanyNewsType",
    "CompanyProductType",
    "DirectorType",
    "ManagementTeamStaffType",
    "ShareholderType",
}

FIELD_RE = re.compile(
    r"^\s*private\s+(?!static\b)(?P<type>[\w<>?, ]+)\s+(?P<name>[A-Za-z_][\w]*)\s*(?:=.*)?;"
)


def _strip_comments(text: str) -> str:
    def replace_block(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    without_blocks = re.sub(r"/\*.*?\*/", replace_block, text, flags=re.S)
    return re.sub(r"//.*", "", without_blocks)


def _camel_to_snake(name: str) -> str:
    if "_" in name:
        return "_".join(_camel_to_snake(part) if part else part for part in name.split("_"))
    name = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return name.lower()


def _annotation_value(annotation: str, key: str) -> str | None:
    match = re.search(rf"{key}\s*=\s*\"([^\"]+)\"", annotation)
    if match:
        return match.group(1)
    return None


def _annotation_int(annotation: str, key: str) -> int | None:
    match = re.search(rf"{key}\s*=\s*(\d+)", annotation)
    if match:
        return int(match.group(1))
    return None


def _annotation_bool(annotation: str, key: str) -> bool | None:
    match = re.search(rf"{key}\s*=\s*(true|false)", annotation)
    if match:
        return match.group(1) == "true"
    return None


def _class_name(text: str, path: Path) -> str:
    match = re.search(r"@Entity\b.*?class\s+(\w+)", text, flags=re.S)
    if match:
        return match.group(1)
    return path.stem


def _table_name(text: str, class_name: str) -> str:
    match = re.search(r"@Table\s*\(\s*name\s*=\s*\"([^\"]+)\"", text)
    if match:
        return match.group(1)
    return class_name.lower()


def _enum_values(java_type: str) -> list[str] | None:
    if java_type not in ENUM_TYPES:
        return None
    path = ENUMS_PATH / f"{java_type}.java"
    text = _strip_comments(path.read_text(encoding="utf-8"))
    match = re.search(rf"enum\s+{java_type}\b.*?\{{(?P<body>.*?);", text, flags=re.S)
    if not match:
        return None
    values = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", match.group("body"))
    return values or None


def _field_row(
    *,
    rel_path: str,
    module: str,
    class_name: str,
    table: str,
    annotations: list[str],
    java_type: str,
    java_field: str,
    source_line: int,
) -> dict[str, Any] | None:
    if "@Transient" in annotations:
        return None
    annotation_text = " ".join(annotations)
    column_annotation = next((item for item in annotations if item.startswith("@Column")), "")
    join_annotation = next((item for item in annotations if item.startswith("@JoinColumn")), "")
    original_java_type = java_type
    is_simple = java_type in SIMPLE_TYPES or java_type in ENUM_TYPES
    if not is_simple and not join_annotation:
        return None

    column = _annotation_value(column_annotation, "name")
    if not column and join_annotation:
        column = _annotation_value(join_annotation, "name")
    if not column:
        column = _camel_to_snake(java_field)

    is_join_column = not column_annotation and bool(join_annotation)
    pydantic_name = _camel_to_snake(column) if is_join_column else _camel_to_snake(java_field)

    nullable = _annotation_bool(column_annotation or join_annotation, "nullable")
    return {
        "class_name": class_name,
        "column": column,
        "definition": _annotation_value(column_annotation, "columnDefinition"),
        "enum_values": _enum_values(java_type),
        "is_join_column": is_join_column,
        "java_field": java_field,
        "java_type": java_type,
        "join_target_class": original_java_type if is_join_column and not is_simple else None,
        "length": _annotation_int(column_annotation, "length"),
        "nullable": True if nullable is None else nullable,
        "primary_key": "@Id" in annotation_text,
        "pydantic_name": pydantic_name,
        "schema_module": module,
        "source_file": rel_path,
        "source_line": source_line,
        "table": table,
    }


def scan_file(rel_path: str) -> list[dict[str, Any]]:
    path = BACKEND_MODELS / rel_path
    text = _strip_comments(path.read_text(encoding="utf-8"))
    class_name = _class_name(text, path)
    table = _table_name(text, class_name)
    module = IN_SCOPE[rel_path]
    rows: list[dict[str, Any]] = []
    annotations: list[str] = []

    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("@"):
            annotations.append(stripped)
            continue
        match = FIELD_RE.match(line)
        if not match:
            if stripped and not stripped.startswith(("public ", "protected ", "private ")):
                annotations = []
            continue
        java_type = " ".join(match.group("type").split())
        row = _field_row(
            rel_path=rel_path,
            module=module,
            class_name=class_name,
            table=table,
            annotations=annotations,
            java_type=java_type,
            java_field=match.group("name"),
            source_line=line_number,
        )
        if row is not None:
            rows.append(row)
        annotations = []
    return rows


def scan() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rel_path in sorted(IN_SCOPE):
        rows.extend(scan_file(rel_path))
    id_types = {
        str(row["class_name"]): str(row["java_type"])
        for row in rows
        if bool(row.get("primary_key"))
    }
    for row in rows:
        target_class = row.get("join_target_class")
        if bool(row.get("is_join_column")) and target_class:
            row["java_type"] = id_types.get(
                str(target_class),
                "String" if str(row["column"]).endswith("_sort_id") else "Long",
            )
            row["enum_values"] = None
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["class_name"]), str(row["table"]), str(row["column"]))
        existing = unique.get(key)
        if existing is None or (
            bool(existing.get("is_join_column")) and not bool(row.get("is_join_column"))
        ):
            unique[key] = row
    return sorted(
        unique.values(),
        key=lambda item: (
            str(item["schema_module"]),
            str(item["class_name"]),
            str(item["table"]),
            str(item["column"]),
            str(item["pydantic_name"]),
        ),
    )


def main() -> None:
    rows = scan()
    payload = json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    OUTPUT_PATH.write_text(payload, encoding="utf-8")
    PACKAGE_OUTPUT_PATH.write_text(payload, encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
