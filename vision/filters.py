"""One Euro filter (Casiez et al. 2012), vectorised and NaN-aware."""
from __future__ import annotations

import math

import numpy as np


class OneEuroFilter:
    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.007, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self) -> None:
        self._x = None
        self._dx = None
        self._t = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t: float) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        if self._x is None:
            self._x, self._dx, self._t = x.copy(), np.zeros_like(x), t
            return x.copy()
        dt = t - self._t
        if dt <= 0:
            return self._x.copy()
        self._t = t
        prev = self._x
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * (x - prev) / dt + (1 - a_d) * self._dx
        a = self._alpha(self.min_cutoff + self.beta * np.abs(dx_hat), dt)
        x_hat = a * x + (1 - a) * prev
        # a coordinate that was missing restarts at the raw value
        x_hat = np.where(np.isnan(prev) & ~np.isnan(x), x, x_hat)
        self._x = x_hat
        self._dx = np.where(np.isnan(dx_hat), 0.0, dx_hat)
        return x_hat.copy()
