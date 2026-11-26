"""Yield curve with linear interpolation, flat extrapolation, parallel shifts and a floor."""
from __future__ import annotations

import numpy as np


class YieldCurve:
    def __init__(self, tenors, rates):
        tenors = np.asarray(tenors, dtype=float)
        rates = np.asarray(rates, dtype=float)
        if len(tenors) == 0 or len(tenors) != len(rates):
            raise ValueError("Yield curve needs matching, non-empty tenors and rates")
        order = np.argsort(tenors)
        self.tenors = tenors[order]
        self.rates = rates[order]

    def rate(self, t, shift: float = 0.0, floor: float | None = None):
        """Annual effective rate at tenor(s) t after a parallel shift, floored."""
        r = np.interp(t, self.tenors, self.rates) + shift
        if floor is not None:
            r = np.maximum(r, floor)
        return r

    def discount_factors(self, times, shift: float = 0.0, spread: float = 0.0, floor: float | None = None):
        times = np.asarray(times, dtype=float)
        r = self.rate(times, shift, floor) + spread
        return (1.0 + r) ** (-times)
