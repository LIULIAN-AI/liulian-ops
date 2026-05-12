from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from inspect import isawaitable
from json import dumps
from typing import Any, ClassVar, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from crawler.harness.budget import Budget, BudgetExceededError
from crawler.harness.checkpoint import JsonValue
from crawler.harness.distill import redact_json
from crawler.harness.planner import PlannedStep, WorkflowName
from crawler.harness.run_types import StepResult

T = TypeVar("T")
ToolCallable = Callable[..., object | Awaitable[object]]


class AgentError(RuntimeError):
    """Base class for agent harness failures."""


class AgentContractError(AgentError):
    """Raised when an agent does not satisfy the harness contract."""


class AgentToolError(AgentError):
    """Raised when an agent attempts to use an unavailable or unsafe tool."""


class AgentBudgetError(AgentError, BudgetExceededError):
    """Raised when an agent exceeds its configured budget."""

    def __init__(self, cause: BudgetExceededError) -> None:
        BudgetExceededError.__init__(self, cause.dimension, cause.limit, cause.used)
        self.cause = cause


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    tool_names: tuple[str, ...] = Field(default_factory=tuple)
    temperature: float = Field(default=0, ge=0, le=2)
    allow_mysql_writes: bool = False


class AgentInputEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_name: str = Field(min_length=1)
    agent_role: str = Field(min_length=1)
    step: PlannedStep
    params: dict[str, JsonValue] = Field(default_factory=dict)
    temperature: float = Field(default=0, ge=0, le=2)
    dedup_key: str = Field(min_length=1)


class AgentOutputEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    state: dict[str, JsonValue] = Field(default_factory=dict)
    cursor: str | None = None
    turns: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))

    @field_validator("usd", mode="before")
    @classmethod
    def _coerce_usd(cls, value: object) -> object:
        if isinstance(value, float):
            return Decimal(str(value))
        return value

    def to_step_result(self) -> StepResult:
        return StepResult(
            metadata=redact_json(self.metadata),
            state=redact_json(self.state),
            cursor=self.cursor,
            turns=self.turns,
            tokens=self.tokens,
            usd=self.usd,
        )


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    callable: ToolCallable
    writes_mysql: bool = False


class ToolRegistry:
    def __init__(self, tools: Mapping[str, ToolDefinition] | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = dict(tools or {})

    def register(
        self,
        name: str,
        func: ToolCallable,
        *,
        writes_mysql: bool = False,
    ) -> None:
        if not name:
            raise AgentToolError("Tool name must be non-empty")
        if name in self._tools:
            raise AgentToolError(f"Tool {name!r} is already registered")
        self._tools[name] = ToolDefinition(name=name, callable=func, writes_mysql=writes_mysql)

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise AgentToolError(f"Tool {name!r} is not registered") from exc


class ToolContext:
    __slots__ = ("__allow_mysql_writes", "__allowed_tool_names", "__budget", "__registry")

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        allowed_tool_names: tuple[str, ...],
        budget: Budget | None = None,
        allow_mysql_writes: bool = False,
    ) -> None:
        self.__registry = registry
        self.__allowed_tool_names = frozenset(allowed_tool_names)
        self.__budget = budget
        self.__allow_mysql_writes = allow_mysql_writes

    async def call_tool(self, name: str, *args: object, **kwargs: object) -> object:
        tool = self._authorize(name)
        self._check_budget()
        result = tool.callable(*args, **kwargs)
        if isawaitable(result):
            result = await result
        self._check_budget()
        return result

    def call_tool_sync(self, name: str, *args: object, **kwargs: object) -> object:
        tool = self._authorize(name)
        self._check_budget()
        result = tool.callable(*args, **kwargs)
        if isawaitable(result):
            close = getattr(result, "close", None)
            if callable(close):
                close()
            raise AgentToolError(f"Tool {name!r} returned an awaitable; use call_tool instead")
        self._check_budget()
        return result

    def _authorize(self, name: str) -> ToolDefinition:
        if name not in self.__allowed_tool_names:
            raise AgentToolError(f"Tool {name!r} is not declared for this agent")
        tool = self.__registry.get(name)
        if tool.writes_mysql and not self.__allow_mysql_writes:
            raise AgentToolError(f"Tool {name!r} writes to MySQL and is not allowed by default")
        return tool

    def _check_budget(self) -> None:
        if self.__budget is None:
            return
        try:
            self.__budget.check()
        except BudgetExceededError as exc:
            raise AgentBudgetError(exc) from exc


class SubAgent(ABC):
    config: ClassVar[AgentConfig]

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        budget: Budget | None = None,
        config: AgentConfig | None = None,
    ) -> None:
        resolved_config = config if config is not None else getattr(self, "config", None)
        if not isinstance(resolved_config, AgentConfig):
            raise AgentContractError("SubAgent subclasses must declare an AgentConfig")
        self._config = resolved_config
        registry = registry or ToolRegistry()
        self._context = ToolContext(
            registry=registry,
            allowed_tool_names=self._config.tool_names,
            budget=budget,
            allow_mysql_writes=self._config.allow_mysql_writes,
        )
        self._budget = budget

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def role(self) -> str:
        return self._config.role

    @property
    def tool_names(self) -> tuple[str, ...]:
        return self._config.tool_names

    @property
    def temperature(self) -> float:
        return self._config.temperature

    async def run(self, step: PlannedStep) -> StepResult:
        envelope = self._input_envelope(step)
        self._check_budget()
        output = cast(
            AgentOutputEnvelope | Mapping[str, Any],
            await _maybe_await(self.execute(envelope, self._context)),
        )
        result = _coerce_output(output).to_step_result()
        self._check_budget()
        return result

    @abstractmethod
    def execute(
        self,
        envelope: AgentInputEnvelope,
        context: ToolContext,
    ) -> AgentOutputEnvelope | Mapping[str, Any] | Awaitable[AgentOutputEnvelope | Mapping[str, Any]]:
        """Execute one planned step and return an output envelope."""

    def _input_envelope(self, step: PlannedStep) -> AgentInputEnvelope:
        return AgentInputEnvelope(
            agent_name=self._config.name,
            agent_role=self._config.role,
            step=step,
            params=dict(step.params),
            temperature=self._config.temperature,
            dedup_key=step.dedup_key,
        )

    def _check_budget(self) -> None:
        if self._budget is None:
            return
        try:
            self._budget.check()
        except BudgetExceededError as exc:
            raise AgentBudgetError(exc) from exc


def make_dedup_key(
    workflow: WorkflowName,
    handler: str,
    params: Mapping[str, JsonValue] | None = None,
) -> str:
    clean_params = {key: value for key, value in dict(params or {}).items() if value is not None}
    suffix = _stable_suffix(clean_params)
    step_id = handler if suffix == "empty" else f"{handler}:{suffix}"
    return f"{workflow}:{step_id}"


def _coerce_output(value: AgentOutputEnvelope | Mapping[str, Any]) -> AgentOutputEnvelope:
    if isinstance(value, AgentOutputEnvelope):
        return value
    try:
        return AgentOutputEnvelope.model_validate(value)
    except Exception as exc:
        raise AgentContractError(f"Agent returned invalid output envelope: {exc}") from exc


def _stable_suffix(value: Mapping[str, JsonValue]) -> str:
    canonical = dumps(value, sort_keys=True, separators=(",", ":"))
    if canonical == "{}":
        return "empty"
    return sha256(canonical.encode("utf-8")).hexdigest()[:12]


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if isawaitable(value):
        return cast(T, await value)
    return value
