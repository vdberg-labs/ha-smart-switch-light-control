import time


class Ramp:
    def __init__(self, start_pct=0, direction=1, period=4.0, min_pct=1):
        self.period = period
        self.min_pct = min_pct
        # Rising half is phase 0.0->0.5, falling half is 0.5->1.0.
        # Seed the clock so we begin at start_pct moving in `direction`.
        frac = start_pct / 100
        if direction >= 1:
            phase = frac * 0.5
        else:
            phase = 1 - (frac * 0.5)
        self.start = time.time() - phase * self.period

    def state(self):
        """Returns (brightness_pct, current_direction). Never below min_pct: 0 would switch the light off."""
        t = (time.time() - self.start) % self.period
        phase = t / self.period
        if phase < 0.5:
            return max(self.min_pct, int(phase * 2 * 100)), 1
        return max(self.min_pct, int((1 - phase) * 2 * 100)), -1
