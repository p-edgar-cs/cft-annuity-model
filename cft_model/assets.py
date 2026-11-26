"""Asset portfolio: fixed-rate, non-callable bonds plus a cash balance.

Simplifications (documented in the README):
* coupons are paid every projection period at the period-equivalent of the
  annual coupon rate, (1 + coupon) ** dt - 1, so annual and monthly runs agree;
* bonds are held at book value with no amortisation; any difference between
  book and par is realised at maturity;
* bonds are priced by discounting at the shifted Treasury curve + their spread.
"""
from __future__ import annotations

import numpy as np

from .curve import YieldCurve


class Portfolio:
    def __init__(self, par, coupon, remaining_periods, spread, book, dt: float, cash: float = 0.0):
        self.par = np.asarray(par, float).copy()
        self.coupon = np.asarray(coupon, float).copy()
        self.remaining = np.asarray(remaining_periods, int).copy()   # periods until maturity
        self.spread = np.asarray(spread, float).copy()
        self.book = np.asarray(book, float).copy()
        self.dt = dt
        self.cash = float(cash)

    # ---------------------------------------------------------------- setup
    @classmethod
    def from_inputs(cls, assets, dt: float, scale: float = 1.0) -> "Portfolio":
        remaining = np.maximum(1, np.round(assets.maturity / dt)).astype(int)
        return cls(assets.par * scale, assets.coupon, remaining, assets.spread, assets.book_value * scale, dt)

    # ------------------------------------------------------------- measures
    @property
    def book_total(self) -> float:
        return float(self.book.sum()) + self.cash

    def earned_rate(self, short_rate: float) -> float:
        """Book yield of the portfolio (bond coupons plus interest on cash)."""
        income = float(np.sum(self.par * self.coupon)) + self.cash * short_rate
        base = float(self.book.sum()) + self.cash
        return income / base if base > 1e-9 else short_rate

    def bond_market_values(self, curve: YieldCurve, shift: float, floor: float | None) -> np.ndarray:
        if len(self.par) == 0:
            return np.zeros(0)
        horizon = int(self.remaining.max())
        times = np.arange(1, horizon + 1) * self.dt
        mv = np.empty(len(self.par))
        for s in np.unique(np.round(self.spread, 6)):
            mask = np.isclose(np.round(self.spread, 6), s)
            df = curve.discount_factors(times, shift, s, floor)
            cum = np.cumsum(df)
            m = self.remaining[mask] - 1
            mv[mask] = self.par[mask] * (self.period_coupon[mask] * cum[m] + df[m])
        return mv

    def market_value(self, curve: YieldCurve, shift: float, floor: float | None) -> float:
        return float(self.bond_market_values(curve, shift, floor).sum()) + self.cash

    @property
    def period_coupon(self) -> np.ndarray:
        return (1.0 + self.coupon) ** self.dt - 1.0

    # ---------------------------------------------------------- operations
    def accrue_cash(self, short_rate: float, borrow_spread: float) -> float:
        rate = short_rate if self.cash >= 0 else short_rate + borrow_spread
        interest = self.cash * ((1 + rate) ** self.dt - 1)
        return interest

    def roll_forward(self) -> tuple[float, float, float]:
        """Pay one period of coupons and redeem maturing bonds.
        Returns (coupons, maturity proceeds, gain/loss vs book on maturity)."""
        coupons = float(np.sum(self.par * self.period_coupon))
        self.remaining -= 1
        matured = self.remaining <= 0
        proceeds = float(self.par[matured].sum())
        gain = proceeds - float(self.book[matured].sum())
        keep = ~matured
        for name in ("par", "coupon", "remaining", "spread", "book"):
            setattr(self, name, getattr(self, name)[keep])
        return coupons, proceeds, gain

    def buy(self, amount: float, coupon: float, term_periods: int, spread: float) -> None:
        if amount <= 0:
            return
        self.par = np.append(self.par, amount)
        self.coupon = np.append(self.coupon, coupon)
        self.remaining = np.append(self.remaining, max(1, term_periods))
        self.spread = np.append(self.spread, spread)
        self.book = np.append(self.book, amount)
        self.cash -= amount

    def sell_to_raise(self, needed: float, curve: YieldCurve, shift: float, floor: float | None) -> tuple[float, float, float]:
        """Sell a pro-rata slice of every bond at market value.
        Returns (proceeds, realised gain/loss vs book, MV-weighted remaining periods
        of the bonds sold - used to amortise the gain through the IMR)."""
        mv = self.bond_market_values(curve, shift, floor)
        total = float(mv.sum())
        if needed <= 0 or total <= 0:
            return 0.0, 0.0, 1.0
        avg_remaining = float(np.sum(mv * self.remaining) / total)
        k = min(1.0, needed / total)
        proceeds = k * total
        gain = proceeds - k * float(self.book.sum())
        self.par *= 1 - k
        self.book *= 1 - k
        self.cash += proceeds
        if k >= 1.0:
            for name in ("par", "coupon", "remaining", "spread", "book"):
                setattr(self, name, getattr(self, name)[:0])
        return proceeds, gain, max(1.0, avg_remaining)
