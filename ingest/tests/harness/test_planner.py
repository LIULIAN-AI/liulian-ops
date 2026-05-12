from __future__ import annotations

from crawler.harness.planner import DagPlanner, plan_workflow


def test_refresh_field_group_plan_has_deterministic_dependency_ordering() -> None:
    first = plan_workflow(
        "refresh_field_group",
        bank_id="bank-1",
        field_group="marketing",
        sort_id=7,
    )
    second = plan_workflow(
        "refresh_field_group",
        sort_id=7,
        field_group="marketing",
        bank_id="bank-1",
    )

    assert [step.handler for step in first.steps] == ["refresh_company", "refresh_field_group"]
    assert first.steps[1].depends_on == (first.steps[0].step_id,)
    assert [step.step_id for step in first.steps] == [step.step_id for step in second.steps]
    assert [step.dedup_key for step in first.steps] == [step.dedup_key for step in second.steps]


def test_re_extract_changed_snapshots_deduplicates_each_snapshot_with_shared_discovery() -> None:
    plan = DagPlanner().plan(
        "re_extract_changed_snapshots",
        snapshot_ids=["snap-b", "snap-a"],
        field_group="compliance",
    )

    assert [step.handler for step in plan.steps] == [
        "discover_changed_snapshots",
        "re_extract_snapshot",
        "re_extract_snapshot",
    ]
    assert plan.steps[1].depends_on == (plan.steps[0].step_id,)
    assert plan.steps[2].depends_on == (plan.steps[0].step_id,)
    assert len({step.dedup_key for step in plan.steps}) == 3
