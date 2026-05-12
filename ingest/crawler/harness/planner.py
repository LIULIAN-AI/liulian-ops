from __future__ import annotations

from collections.abc import Iterable, Sequence
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from crawler.harness.checkpoint import JsonValue

WorkflowName = Literal[
    "discover_banks",
    "refresh_company",
    "refresh_field_group",
    "re_extract_changed_snapshots",
]


class PlannedStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    step_id: str = Field(min_length=1)
    workflow: WorkflowName
    handler: str = Field(min_length=1)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    depends_on: tuple[str, ...] = Field(default_factory=tuple)
    dedup_key: str = Field(min_length=1)
    sequence: int = Field(ge=1)


class WorkflowPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow: WorkflowName
    checkpoint_key: str = Field(min_length=1)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    steps: tuple[PlannedStep, ...]

    def ordered_steps(self) -> tuple[PlannedStep, ...]:
        return self.steps


class DagPlanner:
    def plan(
        self,
        workflow: WorkflowName,
        *,
        checkpoint_key: str | None = None,
        **params: JsonValue,
    ) -> WorkflowPlan:
        clean_params: dict[str, JsonValue] = {
            key: value for key, value in params.items() if value is not None
        }
        steps = tuple(_plan_steps(workflow, clean_params))
        return WorkflowPlan(
            workflow=workflow,
            checkpoint_key=checkpoint_key or _checkpoint_key(workflow, clean_params),
            params=clean_params,
            steps=steps,
        )


def plan_workflow(
    workflow: WorkflowName,
    *,
    checkpoint_key: str | None = None,
    **params: JsonValue,
) -> WorkflowPlan:
    return DagPlanner().plan(workflow, checkpoint_key=checkpoint_key, **params)


def _plan_steps(workflow: WorkflowName, params: dict[str, JsonValue]) -> Iterable[PlannedStep]:
    if workflow == "discover_banks":
        yield _step(workflow, "discover_banks", params, sequence=1)
        return

    if workflow == "refresh_company":
        yield _step(workflow, "refresh_company", params, sequence=1)
        return

    if workflow == "refresh_field_group":
        company_params = _subset(params, ("bank_id", "company_id", "sort_id"))
        refresh_company = _step(workflow, "refresh_company", company_params, sequence=1)
        yield refresh_company
        yield _step(
            workflow,
            "refresh_field_group",
            params,
            sequence=2,
            depends_on=(refresh_company.step_id,),
        )
        return

    if workflow == "re_extract_changed_snapshots":
        discover = _step(workflow, "discover_changed_snapshots", params, sequence=1)
        yield discover
        snapshot_ids = params.get("snapshot_ids")
        if isinstance(snapshot_ids, Sequence) and not isinstance(snapshot_ids, str):
            for index, snapshot_id in enumerate(snapshot_ids, start=2):
                yield _step(
                    workflow,
                    "re_extract_snapshot",
                    {"snapshot_id": _json_scalar(snapshot_id), **_subset(params, ("field_group",))},
                    sequence=index,
                    depends_on=(discover.step_id,),
                )
        else:
            yield _step(
                workflow,
                "re_extract_changed_snapshots",
                params,
                sequence=2,
                depends_on=(discover.step_id,),
            )
        return


def _step(
    workflow: WorkflowName,
    handler: str,
    params: dict[str, JsonValue],
    *,
    sequence: int,
    depends_on: tuple[str, ...] = (),
) -> PlannedStep:
    suffix = _stable_suffix(params)
    step_id = handler if suffix == "empty" else f"{handler}:{suffix}"
    return PlannedStep(
        step_id=step_id,
        workflow=workflow,
        handler=handler,
        params=params,
        depends_on=depends_on,
        dedup_key=f"{workflow}:{step_id}",
        sequence=sequence,
    )


def _checkpoint_key(workflow: WorkflowName, params: dict[str, JsonValue]) -> str:
    suffix = _stable_suffix(params)
    return workflow if suffix == "empty" else f"{workflow}:{suffix}"


def _stable_suffix(value: JsonValue | dict[str, JsonValue]) -> str:
    canonical = _canonical_json(value)
    if canonical == "{}":
        return "empty"
    return sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _canonical_json(value: JsonValue | dict[str, JsonValue]) -> str:
    from json import dumps

    return dumps(value, sort_keys=True, separators=(",", ":"))


def _subset(params: dict[str, JsonValue], keys: Iterable[str]) -> dict[str, JsonValue]:
    return {key: params[key] for key in keys if key in params}


def _json_scalar(value: object) -> JsonValue:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
