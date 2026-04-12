from dataclasses import dataclass
from typing import Optional, List
from config import THRESHOLDS


@dataclass
class AnomalyEvent:
    threshold: int
    side: str        # "Up" or "Down"
    price: float
    count: int


class AnomalyCalculator:
    def __init__(self):
        self.reset()

    def reset(self):
        self._state = {t: {"last_side": None, "count": 0} for t in THRESHOLDS}

    def check(self, up_ask: float, down_ask: float) -> List[AnomalyEvent]:
        """
        For each threshold T, count oscillations between Up and Down sides.
        An anomaly is counted each time the active side switches AFTER both sides
        have touched the threshold. >= threshold means the side is priced at
        that confidence level or higher (e.g., 0.60 = 60% probability).

        Guard: only count if combined best ask < 1.10 (market is liquid/fair).
        When combined >= 1.10 the spread is too wide — the crossing is noise, not signal.
        """
        if up_ask + down_ask >= 1.10:
            return []

        events = []
        for t in THRESHOLDS:
            tp = t / 100.0
            state = self._state[t]

            if up_ask >= tp:
                if state["last_side"] is None:
                    state["last_side"] = "Up"
                elif state["last_side"] == "Down":
                    state["count"] += 1
                    state["last_side"] = "Up"
                    events.append(AnomalyEvent(t, "Up", up_ask, state["count"]))

            if down_ask >= tp:
                if state["last_side"] is None:
                    state["last_side"] = "Down"
                elif state["last_side"] == "Up":
                    state["count"] += 1
                    state["last_side"] = "Down"
                    events.append(AnomalyEvent(t, "Down", down_ask, state["count"]))

        return events

    def get_counts(self) -> dict:
        return {t: self._state[t]["count"] for t in THRESHOLDS}

    def get_last_sides(self) -> dict:
        return {t: self._state[t]["last_side"] for t in THRESHOLDS}
