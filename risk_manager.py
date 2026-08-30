"""
risk_manager.py — Kotegawa-style risk management.

Modeled on the discipline attributed to Takashi Kotegawa ("BNF"), the Japanese
day trader who turned ~1.6M yen into over 15B yen primarily through:

  1. Risking a small, FIXED % of capital per trade (1-2%), never more —
     position size is derived from the stop distance, not from a gut feel.
  2. Never over-leveraging a single position — even a very tight stop can't
     push one trade's capital allocation past a hard ceiling.
  3. Cutting losses fast and without hesitation — stops are tighter than a
     "let it breathe" swing-trader stop, and every setup must clear a
     minimum reward:risk before it's taken at all.
  4. Treating capital preservation as the #1 job — an aggregate/daily loss
     circuit breaker halts new entries once losses reach a set % of capital,
     and a max-open-positions cap prevents correlated over-exposure.

This module has no knowledge of stocks/options specifically — it just turns
(entry, stop, target, capital) into a position size and a set of pass/fail
risk checks. engine.py wires it into the actual scan/signal logic.
"""

import os
from dataclasses import dataclass


@dataclass
class PositionSize:
    qty: int                # units for equities, or lots*lot_size for options
    lots: int                # 1 for equities; number of option lots
    risk_amount: float       # ₹ actually at risk if stop is hit
    position_value: float    # ₹ capital deployed
    risk_per_unit: float
    capped_by: str           # "risk" | "position_cap" | "capital" | "zero"


class RiskManager:
    def __init__(
        self,
        capital: float,
        risk_per_trade_pct: float = None,
        max_position_pct: float = None,
        max_daily_loss_pct: float = None,
        max_open_positions: int = None,
        min_reward_risk: float = None,
    ):
        self.capital = float(capital)

        # Kotegawa risked no more than 1-2% of capital per trade.
        self.risk_per_trade_pct = (
            risk_per_trade_pct if risk_per_trade_pct is not None
            else float(os.getenv("RISK_PER_TRADE_PCT", 1.0))
        )
        # Hard ceiling on capital in any single name/contract, regardless of
        # how tight the stop makes the "risk-sized" quantity look.
        self.max_position_pct = (
            max_position_pct if max_position_pct is not None
            else float(os.getenv("MAX_POSITION_PCT", 20.0))
        )
        # Circuit breaker: stop opening new trades once today's realized
        # losses hit this % of capital. This is the "live to trade tomorrow"
        # rule — it is what actually prevents a blow-up account.
        self.max_daily_loss_pct = (
            max_daily_loss_pct if max_daily_loss_pct is not None
            else float(os.getenv("MAX_DAILY_LOSS_PCT", 3.0))
        )
        # Cap on simultaneously open positions, so a handful of correlated
        # names/contracts can't together exceed the intended risk budget.
        self.max_open_positions = (
            max_open_positions if max_open_positions is not None
            else int(os.getenv("MAX_OPEN_POSITIONS", 6))
        )
        # Reject setups whose payoff doesn't clear a minimum reward:risk —
        # cutting losses fast only works if wins are structurally bigger.
        self.min_reward_risk = (
            min_reward_risk if min_reward_risk is not None
            else float(os.getenv("MIN_REWARD_RISK", 1.5))
        )

    # ---- budgets -------------------------------------------------------
    def risk_amount(self) -> float:
        return self.capital * (self.risk_per_trade_pct / 100.0)

    def max_position_value(self) -> float:
        return self.capital * (self.max_position_pct / 100.0)

    def daily_loss_limit(self) -> float:
        return self.capital * (self.max_daily_loss_pct / 100.0)

    # ---- position sizing -------------------------------------------------
    def position_size(self, entry: float, stop: float, lot_size: int = 1) -> PositionSize:
        """
        Size a position so that, if the stop is hit, the loss equals
        risk_per_trade_pct of capital — then clamp it so the position value
        never exceeds max_position_pct of capital, and never exceeds what
        the account can actually pay for.

        For options, pass lot_size (contract size); qty/lots are rounded
        down to whole lots. Returns qty=0 if the setup can't be sized safely
        (e.g. stop distance is zero/invalid, or can't afford even one lot).
        """
        risk_per_unit = abs(entry - stop)
        if entry <= 0 or risk_per_unit <= 0:
            return PositionSize(0, 0, 0.0, 0.0, risk_per_unit, "zero")

        risk_amt = self.risk_amount()
        raw_units = risk_amt / risk_per_unit
        units = self._round_to_lot(raw_units, lot_size)
        capped_by = "risk"

        max_val = self.max_position_value()
        if units * entry > max_val:
            capped_units = self._round_to_lot(max_val / entry, lot_size)
            if capped_units < units:
                units, capped_by = capped_units, "position_cap"

        if units * entry > self.capital:
            capped_units = self._round_to_lot(self.capital / entry, lot_size)
            if capped_units < units:
                units, capped_by = capped_units, "capital"

        if lot_size > 1 and units < lot_size:
            units, capped_by = 0, "zero"

        lots = (units // lot_size) if lot_size > 1 else 1 if units > 0 else 0

        return PositionSize(
            qty=int(units),
            lots=int(lots),
            risk_amount=round(units * risk_per_unit, 2),
            position_value=round(units * entry, 2),
            risk_per_unit=round(risk_per_unit, 4),
            capped_by=capped_by,
        )

    @staticmethod
    def _round_to_lot(raw_units: float, lot_size: int) -> int:
        if raw_units <= 0:
            return 0
        if lot_size > 1:
            return int(raw_units // lot_size) * lot_size
        return int(raw_units)

    # ---- setup-quality gate ----------------------------------------------
    def passes_reward_risk(self, entry: float, stop: float, target: float) -> bool:
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0:
            return False
        return (reward / risk) >= self.min_reward_risk

    # ---- account-level circuit breakers ------------------------------
    def circuit_breaker_tripped(self, realized_daily_pnl: float) -> bool:
        """True once today's realized loss has reached the daily loss limit."""
        return realized_daily_pnl <= -self.daily_loss_limit()

    def open_positions_allowed(self, current_open_count: int) -> bool:
        return current_open_count < self.max_open_positions
