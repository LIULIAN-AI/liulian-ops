# U01 — Pydantic schemas (mirrors of Java entities) + parity scanner

## Purpose

Produce the canonical Pydantic v2 models that every other unit in
`neobanker-crawler/` consumes. They MUST stay in 1:1 lock-step with the
`@Entity`-annotated Java classes in
`neobanker-backend-MVP-V2/src/main/java/com/neobanker/neobank/models/`,
because every value the crawler writes is going to be serialized as one
of these and POSTed to the backend's future
`/internal/crawler/upsert` endpoint. Drift between the two sides is the
single largest source of bugs we want to engineer out.

A **parity scanner** (`tools/scan_java_columns.py`) reads the Java files
and emits the authoritative `(table, column, type, nullable)` set; a
matching `pytest` test asserts the Pydantic side matches. CI fails on
any drift.

## Scope (in)

Only the entities the crawler reads or writes. **Skip** profile/account/
search/Homepage entities and any DTOs.

Mandatory entities (one Pydantic model per Java class, same field names
in `snake_case`, same nullability, same string lengths preserved as
`Field(..., max_length=N)`):

```
company/Company.java                  -> schemas/company.py::Company
company/Finance.java                  -> schemas/company.py::Finance
company/CompanyTag.java               -> schemas/company.py::CompanyTag
company/CompanyNews.java              -> schemas/company.py::CompanyNews
company/CompanyNewsTag.java           -> schemas/company.py::CompanyNewsTag
company/Director.java                 -> schemas/company.py::Director
company/Funding.java                  -> schemas/company.py::Funding
company/Investment.java               -> schemas/company.py::Investment
company/License.java                  -> schemas/company.py::License
company/LicenseObtainment.java        -> schemas/company.py::LicenseObtainment
company/Location.java                 -> schemas/company.py::Location
company/CompanyLocation.java          -> schemas/company.py::CompanyLocation
company/CompanyOwner.java             -> schemas/company.py::CompanyOwner
company/IOSApp.java                   -> schemas/company.py::IOSApp
company/Marketing.java                -> schemas/company.py::Marketing
company/Web3.java                     -> schemas/company.py::Web3
company/Shareholder.java              -> schemas/company.py::Shareholder
company/ShareholderTag.java           -> schemas/company.py::ShareholderTag
company/ManagementTeamStaff.java      -> schemas/company.py::ManagementTeamStaff
company/ManagementTeamTag.java        -> schemas/company.py::ManagementTeamTag
company/Report.java                   -> schemas/company.py::Report
company/Campaign.java                 -> schemas/company.py::Campaign
company/CampaignUrl.java              -> schemas/company.py::CampaignUrl

compliance/Initiative.java            -> schemas/compliance.py::Initiative
compliance/Regulatory.java            -> schemas/compliance.py::Regulatory

financials/Financials.java            -> schemas/financials.py::Financials
financials/FFunding.java              -> schemas/financials.py::FFunding
financials/FInvestment.java           -> schemas/financials.py::FInvestment
financials/FinancialData.java         -> schemas/financials.py::FinancialData
financials/Bank.java                  -> schemas/financials.py::Bank
financials/InvestorInfo.java          -> schemas/financials.py::InvestorInfo

marketing/MarketingAppLatestRecord.java       -> schemas/marketing.py::MarketingAppLatestRecord
marketing/MarketingBaseHeader.java            -> schemas/marketing.py::MarketingBaseHeader
marketing/MarketingMediaInfo.java             -> schemas/marketing.py::MarketingMediaInfo
marketing/MarketingSocialMediaDetail.java     -> schemas/marketing.py::MarketingSocialMediaDetail

product/BankAccount.java              -> schemas/product.py::BankAccount
product/Card.java                     -> schemas/product.py::Card
product/CardWelfare.java              -> schemas/product.py::CardWelfare
product/CompanyProduct.java           -> schemas/product.py::CompanyProduct
product/Deposit.java                  -> schemas/product.py::Deposit
product/InterestRate.java             -> schemas/product.py::InterestRate
product/Lending.java                  -> schemas/product.py::Lending
product/Partner.java                  -> schemas/product.py::Partner
product/TransferAndExchange.java      -> schemas/product.py::TransferAndExchange

staff/StaffEmployeeNoAndTech.java     -> schemas/staff.py::StaffEmployeeNoAndTech
staff/StaffManagement.java            -> schemas/staff.py::StaffManagement
staff/StaffShareholder.java           -> schemas/staff.py::StaffShareholder
```

## Scope (out)

- `account/`, `profile/`, `search/`, `Homepage/`, `*Document.java`
  (Elasticsearch projections), `*Key.java` (composite-key wrappers — fold
  them into the parent entity as multiple PK fields), `GlobalSearchResult.java`,
  `CompanySearch.java`.
- Don't generate JPA `@OneToMany` / `@ManyToMany` graph navigation in
  Python — model relations as plain ID references (`company_sort_id: str`)
  unless the related entity is **always written together** as one upsert.
  In that case use a nested optional list (e.g. `Company.shareholders: list[Shareholder] | None`).

## Field-mapping rules (deterministic)

| Java | Pydantic |
|---|---|
| `String name` + `@Column(name="name", length=L)` | `name: str | None = Field(None, max_length=L)` |
| `String` no `@Column` length | `name: str | None = None` |
| `Long` / `long` | `int | None` |
| `int` / `Integer` / `BigInt` | `int | None` |
| `boolean` / `Boolean` / `bit(1)` | `bool | None` |
| `double` / `Double` / `float` | `float | None` |
| `LocalDateTime` / `datetime` | `datetime | None` |
| `text` / `TEXT` / `MEDIUMTEXT` | `str | None` (no max_length) |
| `binary(16)` / UUID | `UUID | None` |
| `enum` (`varchar; enum`) | `Literal[...]` if the enum values are knowable from the Java source; otherwise `str | None` |
| `@Id` field | mark as `Field(..., description="primary key")`; do NOT auto-generate; the backend allocates IDs |
| Java `camelCase` | Python `snake_case`; expose JSON via `model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)` so the wire format matches the Java side |

## Cross-cutting Pydantic config (every model)

```python
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

class _Base(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
        extra="forbid",          # parity-locked: unknown fields fail loudly
        str_strip_whitespace=True,
    )
```

All entity models inherit from `_Base`. `extra="forbid"` is what makes
the parity test bite: any field missing on either side blows up.

## Provenance schema (new, has no Java counterpart)

`schemas/provenance.py`:

```python
class FieldProvenance(_Base):
    company_sort_id: str
    field_name: str            # e.g. "company.bank_swift"
    source_url: str
    snapshot_uri: str          # MinIO URI
    run_id: UUID
    llm_model: str | None      # e.g. "claude-sonnet-4-6" or "ollama:qwen2.5:7b" or None
    confidence: float          # 0..1
    extracted_at: datetime
    prompt_sha: str | None     # git SHA of the Jinja template, if used
```

## Parity scanner — `tools/scan_java_columns.py`

A small standalone Python 3.12 script (no third-party deps beyond
`stdlib`) that:

1. Walks `neobanker-backend-MVP-V2/src/main/java/com/neobanker/neobank/models/`.
2. For each `.java` file, parses with **regex** (don't pull in javalang —
   keep deps zero):
   - class name (`@Entity\s+...class\s+(\w+)`),
   - table name (`@Table\(name\s*=\s*"(\w+)"`) — fallback to `lower(class)`,
   - each `@Column` field: capture `name=`, `length=`, `nullable=`,
     `columnDefinition=`, plus the Java type from the next-line declaration.
3. Emits one row per (table, column) to `tools/_java_columns.json` with
   `{"table","column","java_type","length","nullable","definition","source_file","source_line"}`.

Then a Pytest test
`tests/schemas/test_mirrors_java_entities.py`:

1. Imports every model class registered in `crawler.schemas.__all__`.
2. Cross-references its fields against the JSON dump.
3. Asserts:
   - every Pydantic field maps to a Java `@Column` of the same `snake_case` name,
   - every Java `@Column` for an in-scope entity has a corresponding Pydantic field,
   - `max_length` matches `@Column(length=...)` when present,
   - nullability matches (`Java nullable=true` ↔ Python `Optional`).
4. Failure messages must list the missing/extra fields (don't just `assert False`).

## Self-review pass (Codex MUST run this after implementing, before reporting)

After implementation passes, **re-read your own diff** against this
checklist. Fix any miss in the same run before returning.

- [ ] `tools/scan_java_columns.py` runs to completion and emits
      deterministic JSON.
- [ ] Every Java `@Column` for an in-scope entity has a Pydantic field
      of the same `snake_case` name.
- [ ] Every Pydantic field maps to a Java `@Column` (`extra="forbid"`
      makes this bite).
- [ ] `max_length` matches `@Column(length=...)` whenever present.
- [ ] Nullability matches Java's `nullable=` / lack thereof.
- [ ] Every model inherits from `_Base` and has the same `ConfigDict`.
- [ ] No model has `extra="allow"`.
- [ ] camelCase wire format: `Company.model_dump(by_alias=True)` returns
      `companySortId`, not `company_sort_id`.
- [ ] No file in `src/crawler/schemas/` exceeds 400 lines.
- [ ] `uv run pytest tests/schemas -q` is green.
- [ ] `uv run mypy --strict src/crawler/schemas/` is clean.
- [ ] `tools/scan_java_columns.py` is dependency-free (stdlib only).

In your final report, list which boxes you verified and which (if any)
you couldn't and why.

## Acceptance criteria

- `uv run pytest tests/schemas -q` is green on the local laptop without
  any network access.
- `tools/scan_java_columns.py` outputs deterministic JSON
  (sorted keys, stable ordering).
- `python -c "from crawler.schemas import Company; Company.model_json_schema()"`
  produces a JSON Schema with **camelCase** field names (so Java/Jackson
  on the backend sees what it expects).
- `mypy --strict src/crawler/schemas/` is clean.
- No model has `extra="allow"`.
- Total file size budget: ≤ 400 lines per `schemas/*.py` file (per the
  user's coding-style rule). Split sensibly (e.g. financials.py can stay
  one file; product.py likewise; company.py may need to split into
  `company.py` + `company_extras.py` if over budget).

## Files to read first (mandatory, before writing a single line)

1. `neobanker-backend-MVP-V2/src/main/java/com/neobanker/neobank/models/company/Company.java` — the canonical example.
2. Every `.java` file listed under "Scope (in)" above.
3. `neobanker-backend-MVP-V2/db_tables.txt` — sanity check on table names.
4. `/Users/xiaochong/Downloads/Data组数据说明.xlsx` (sheet `数据表`) — the
   business field dictionary; helpful when an `enum` value list is not
   visible in the Java source.
5. `~/.claude/rules/coding-style.md` — file-size, naming, type-hint, and
   docstring rules. Honor them.

## Out of scope

- No SQLAlchemy models. All DB writes go through the backend HTTP API.
- No Alembic / migrations.
- No ID allocation logic (the backend owns sort_id allocation).
- No relationship-graph traversal helpers.

## Deliverables

```
src/crawler/schemas/__init__.py        # re-exports + __all__ list
src/crawler/schemas/_base.py           # _Base class with ConfigDict
src/crawler/schemas/company.py
src/crawler/schemas/compliance.py
src/crawler/schemas/financials.py
src/crawler/schemas/marketing.py
src/crawler/schemas/product.py
src/crawler/schemas/staff.py
src/crawler/schemas/provenance.py
tools/scan_java_columns.py
tests/schemas/test_mirrors_java_entities.py
tests/schemas/conftest.py              # fixture: load _java_columns.json
pyproject.toml                         # uv project; pydantic>=2.7, pytest>=8, mypy>=1.10, ruff
```
