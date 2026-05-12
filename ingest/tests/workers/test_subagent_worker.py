from __future__ import annotations

import asyncio
from decimal import Decimal

from crawler.agents.base import AgentConfig, AgentInputEnvelope, AgentOutputEnvelope, SubAgent, ToolContext
from crawler.workers.subagent_worker import SubAgentJob, SubAgentWorker, dispatch_subagent_job


class RecordingAgent(SubAgent):
    config = AgentConfig(name="recording", role="records jobs")

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[AgentInputEnvelope] = []

    async def execute(
        self,
        envelope: AgentInputEnvelope,
        context: ToolContext,
    ) -> AgentOutputEnvelope:
        _ = context
        self.seen.append(envelope)
        return AgentOutputEnvelope(
            metadata={"handler": envelope.step.handler},
            state={"ok": True},
            turns=2,
            tokens=11,
            usd=Decimal("0.03"),
        )


class FailingAgent(SubAgent):
    config = AgentConfig(name="failing", role="fails jobs")

    async def execute(
        self,
        envelope: AgentInputEnvelope,
        context: ToolContext,
    ) -> AgentOutputEnvelope:
        _ = envelope, context
        raise RuntimeError("boom")


class RawFailingAgent(SubAgent):
    config = AgentConfig(name="raw_failing", role="fails with raw content")

    async def execute(
        self,
        envelope: AgentInputEnvelope,
        context: ToolContext,
    ) -> AgentOutputEnvelope:
        _ = envelope, context
        raise RuntimeError("<html>secret</html>")


def test_worker_dispatches_decoded_payload_to_registered_subagent() -> None:
    async def run() -> None:
        agent = RecordingAgent()
        worker = SubAgentWorker({"refresh_company": agent})

        result = await worker.dispatch(
            {
                "workflow": "refresh_company",
                "handler": "refresh_company",
                "params": {"company_id": "company-1"},
                "step_id": "refresh_company:unit",
                "dedup_key": "refresh_company:unit",
            }
        )

        assert result.status == "succeeded"
        assert result.turns == 2
        assert result.tokens == 11
        assert agent.seen[0].step.step_id == "refresh_company:unit"
        assert agent.seen[0].params == {"company_id": "company-1"}

    asyncio.run(run())


def test_worker_reports_failures_without_raising() -> None:
    async def run() -> None:
        worker = SubAgentWorker({"refresh_company": FailingAgent()})

        result = await worker.dispatch(
            SubAgentJob(workflow="refresh_company", handler="refresh_company")
        )

        assert result.status == "failed"
        assert result.error == "boom"
        assert result.step_id == "refresh_company"

    asyncio.run(run())


def test_worker_redacts_raw_content_from_failure_errors() -> None:
    async def run() -> None:
        worker = SubAgentWorker({"refresh_company": RawFailingAgent()})

        result = await worker.dispatch(
            SubAgentJob(workflow="refresh_company", handler="refresh_company")
        )

        assert result.status == "failed"
        assert result.error == "[redacted]"

    asyncio.run(run())


def test_worker_redacts_short_raw_marker_failure_errors() -> None:
    class RawTextFailingAgent(SubAgent):
        config = AgentConfig(name="raw_text_failing", role="fails with raw marker")

        async def execute(
            self,
            envelope: AgentInputEnvelope,
            context: ToolContext,
        ) -> AgentOutputEnvelope:
            _ = envelope, context
            raise RuntimeError("raw_text=secret-token")

    async def run() -> None:
        worker = SubAgentWorker({"refresh_company": RawTextFailingAgent()})

        result = await worker.dispatch(
            SubAgentJob(workflow="refresh_company", handler="refresh_company")
        )

        assert result.status == "failed"
        assert result.error == "[redacted]"

    asyncio.run(run())


def test_worker_reports_unknown_handler() -> None:
    async def run() -> None:
        result = await dispatch_subagent_job(
            {"workflow": "refresh_company", "handler": "missing"},
            agents={},
        )

        assert result.status == "unknown_handler"
        assert result.error == "No subagent registered for handler 'missing'"

    asyncio.run(run())
