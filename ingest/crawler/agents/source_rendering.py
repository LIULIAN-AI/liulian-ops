from __future__ import annotations

from collections.abc import Mapping
from string import Formatter
from urllib.parse import quote, urlparse

from crawler.harness.checkpoint import JsonValue


class SourceConfigError(RuntimeError):
    """Raised when source selector configuration is invalid."""


def render_url_template(
    template: str | None,
    company: Mapping[str, JsonValue],
) -> tuple[str | None, tuple[str, ...]]:
    if not template:
        return None, ()
    rendered_parts: list[str] = []
    missing: list[str] = []
    formatter = Formatter()
    for literal, field_name, format_spec, conversion in formatter.parse(template):
        rendered_parts.append(literal)
        if field_name is None:
            continue
        if format_spec or conversion:
            raise SourceConfigError(f"Unsupported URL template format for {field_name!r}")
        value = _lookup_template_value(company, field_name)
        if value is None:
            missing.append(field_name)
            rendered_parts.append("{" + field_name + "}")
            continue
        rendered_parts.append(_render_template_value(template, field_name, value))
    rendered = "".join(rendered_parts)
    _validate_rendered_url(rendered)
    return rendered, tuple(missing)


def render_params(
    params: Mapping[str, JsonValue],
    company: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    rendered: dict[str, JsonValue] = {}
    for key, value in params.items():
        if isinstance(value, str) and "{" in value:
            expanded, missing = render_url_template(value, company)
            rendered[key] = expanded if expanded is not None else value
            if missing:
                rendered[f"{key}_missing_variables"] = list(missing)
        else:
            rendered[key] = value
    return rendered


def _lookup_template_value(company: Mapping[str, JsonValue], field_name: str) -> JsonValue:
    if not field_name.replace("_", "").replace(".", "").isalnum():
        raise SourceConfigError(f"Unsafe URL template variable: {field_name!r}")
    current: object = company
    for part in field_name.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    if isinstance(current, Mapping) or isinstance(current, list):
        raise SourceConfigError(f"URL template variable {field_name!r} is not scalar")
    if isinstance(current, str | int | float | bool) or current is None:
        return current
    raise SourceConfigError(f"URL template variable {field_name!r} is not JSON scalar")


def _render_template_value(template: str, field_name: str, value: JsonValue) -> str:
    if value is None:
        return ""
    raw = str(value).strip()
    if _is_host_placeholder(template, field_name):
        parsed = urlparse(f"https://{raw.strip('/')}")
        if parsed.username or parsed.password or parsed.hostname != raw.strip("/").lower():
            raise SourceConfigError(f"Unsafe host template value for {field_name!r}")
        if any(char in raw for char in ("?", "#", "@", "\\")):
            raise SourceConfigError(f"Unsafe host template value for {field_name!r}")
        return raw.strip("/")
    return quote(raw.strip("/"), safe=":-._~")


def _is_host_placeholder(template: str, field_name: str) -> bool:
    marker = "{" + field_name + "}"
    parsed = urlparse(template.replace(marker, "placeholder.test"))
    return parsed.netloc == "placeholder.test"


def _validate_rendered_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise SourceConfigError(f"Rendered URL is missing host: {value!r}")
