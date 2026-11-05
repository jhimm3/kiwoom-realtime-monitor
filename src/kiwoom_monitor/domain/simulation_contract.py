"""Immutable simulation model documents, shared without the paper engine."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

COST_MODEL_VERSION = "fixed_bps/v1"
COST_MODEL_DECIMAL_VERSION = "fixed_bps/v2"
EXECUTION_MODEL_VERSION = "next_tradable_bar_open/v1"
SAME_BAR_PATH_VERSION = "conservative_with_optimistic_bound/v1"
COMPLETED_CLOSE_EXECUTION_VERSION = "completed_close_then_next_open/v1"
NO_INTRABAR_PATH_VERSION = "no_intrabar_fill/v1"


def _aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("execution timestamps must be timezone-aware")
    return parsed


@dataclass(frozen=True)
class SimulationCostModel:
    version: str
    commission_bps: int | Decimal
    sell_tax_bps: int | Decimal
    slippage_bps: int | Decimal
    rate_basis: str = "unspecified"
    source: str = ""
    valid_from: str = ""
    valid_to: str = ""

    def __post_init__(self) -> None:
        if self.version not in {COST_MODEL_VERSION, COST_MODEL_DECIMAL_VERSION}:
            raise ValueError(f"unregistered cost model: {self.version}")
        for field in ("commission_bps", "sell_tax_bps", "slippage_bps"):
            value = getattr(self, field)
            if self.version == COST_MODEL_VERSION:
                if type(value) is not int:
                    raise ValueError(f"{field} must be an integer in fixed_bps/v1")
            else:
                if not isinstance(value, (str, Decimal)):
                    raise ValueError(f"{field} must be a canonical decimal string in fixed_bps/v2")
                text = str(value)
                if re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?", text) is None:
                    raise ValueError(f"{field} must be a canonical decimal string in fixed_bps/v2")
                try:
                    decimal_value = Decimal(text)
                except InvalidOperation as exc:
                    raise ValueError(f"{field} must be a decimal rate") from exc
                if not decimal_value.is_finite() or decimal_value < 0:
                    raise ValueError(f"{field} must be a canonical nonnegative rate in 0.01 bp units")
                object.__setattr__(self, field, decimal_value)
        if min(self.commission_bps, self.sell_tax_bps, self.slippage_bps) < 0:
            raise ValueError("simulation cost rates must not be negative")
        if self.slippage_bps >= 10_000:
            raise ValueError("slippage_bps must be below 10000")
        if self.rate_basis not in {
            "unspecified", "official_period_rule", "account_actual", "model_estimate",
        }:
            raise ValueError(f"unsupported simulation cost rate basis: {self.rate_basis}")
        if bool(self.valid_from) != bool(self.valid_to):
            raise ValueError("simulation cost validity requires both valid_from and valid_to")
        if self.valid_from:
            start = _aware_datetime(self.valid_from)
            end = _aware_datetime(self.valid_to)
            if end <= start:
                raise ValueError("simulation cost valid_to must be after valid_from")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.version == COST_MODEL_DECIMAL_VERSION:
            for field in ("commission_bps", "sell_tax_bps", "slippage_bps"):
                result[field] = str(result[field])
        return result


@dataclass(frozen=True)
class SimulationExecutionConfig:
    version: str
    same_bar_path_version: str
    initial_cash_won: int
    cost_model: SimulationCostModel | None

    def __post_init__(self) -> None:
        path_models = {
            EXECUTION_MODEL_VERSION: SAME_BAR_PATH_VERSION,
            COMPLETED_CLOSE_EXECUTION_VERSION: NO_INTRABAR_PATH_VERSION,
        }
        if self.version not in path_models:
            raise ValueError(f"unregistered execution model: {self.version}")
        if self.same_bar_path_version != path_models[self.version]:
            raise ValueError(f"unregistered same-bar path model: {self.same_bar_path_version}")
        if self.initial_cash_won <= 0:
            raise ValueError("initial_cash_won must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "same_bar_path_version": self.same_bar_path_version,
            "initial_cash_won": self.initial_cash_won,
            "cost_model": self.cost_model.to_dict() if self.cost_model else None,
        }
