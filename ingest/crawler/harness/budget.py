from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BudgetExceededError(RuntimeError):
    """Raised when crawler harness budget usage exceeds a configured limit."""

    def __init__(self, dimension: str, limit: int | float | Decimal, used: int | float | Decimal) -> None:
        super().__init__(f"Budget exceeded for {dimension}: used {used} > limit {limit}")
        self.dimension = dimension
        self.limit = limit
        self.used = used


class BudgetLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    wall_seconds: float | None = Field(default=None, gt=0)
    tokens: int | None = Field(default=None, gt=0)
    usd: Decimal | None = Field(default=None, gt=Decimal("0"))
    turns: int | None = Field(default=None, gt=0)

    @field_validator("usd", mode="before")
    @classmethod
    def _coerce_usd(cls, value: object) -> object:
        if isinstance(value, float):
            return Decimal(str(value))
        return value


class BudgetUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    wall_seconds: float = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    turns: int = Field(default=0, ge=0)

    @field_validator("usd", mode="before")
    @classmethod
    def _coerce_usd(cls, value: object) -> object:
        if isinstance(value, float):
            return Decimal(str(value))
        return value


class Budget:
    def __init__(
        self,
        limits: BudgetLimits,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._limits = limits
        self._clock = clock
        self._started_at = clock()
        self._tokens = 0
        self._usd = Decimal("0")
        self._turns = 0

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    @property
    def usage(self) -> BudgetUsage:
        return BudgetUsage(
            wall_seconds=self._elapsed_wall_seconds(),
            tokens=self._tokens,
            usd=self._usd,
            turns=self._turns,
        )

    def record_tokens(self, tokens: int) -> BudgetUsage:
        if tokens < 0:
            raise ValueError("tokens must be non-negative")
        self._tokens += tokens
        return self.check()

    def record_usd(self, usd: Decimal | int | str) -> BudgetUsage:
        amount = Decimal(str(usd))
        if amount < 0:
            raise ValueError("usd must be non-negative")
        self._usd += amount
        return self.check()

    def record_turn(self, count: int = 1) -> BudgetUsage:
        if count < 0:
            raise ValueError("turn count must be non-negative")
        self._turns += count
        return self.check()

    def record(
        self,
        *,
        tokens: int = 0,
        usd: Decimal | int | str = Decimal("0"),
        turns: int = 0,
    ) -> BudgetUsage:
        if tokens < 0:
            raise ValueError("tokens must be non-negative")
        amount = Decimal(str(usd))
        if amount < 0:
            raise ValueError("usd must be non-negative")
        if turns < 0:
            raise ValueError("turns must be non-negative")
        self._tokens += tokens
        self._usd += amount
        self._turns += turns
        return self.check()

    def check(self) -> BudgetUsage:
        usage = self.usage
        self._raise_if_exceeded("wall_seconds", self._limits.wall_seconds, usage.wall_seconds)
        self._raise_if_exceeded("tokens", self._limits.tokens, usage.tokens)
        self._raise_if_exceeded("usd", self._limits.usd, usage.usd)
        self._raise_if_exceeded("turns", self._limits.turns, usage.turns)
        return usage

    def _elapsed_wall_seconds(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    @staticmethod
    def _raise_if_exceeded(
        dimension: str,
        limit: int | float | Decimal | None,
        used: int | float | Decimal,
    ) -> None:
        if limit is not None and used > limit:
            raise BudgetExceededError(dimension, limit, used)
