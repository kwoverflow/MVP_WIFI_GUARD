"""Presence configuration and fresh empty-room baseline, independent of UART AGC.

This never flashes firmware or sends the UART-only 'train' instruction to SPI.
The baseline uses the existing MV/Welch equations after 30 s leaving + 30 s
capture. Hardware connection/freshness and a full baseline window are required.
"""
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import threading
import time
import numpy as np
from wifiguard_contracts.control import CalibrationRequest, PresenceSettings
from .calibration.onboarding import _compute_baseline_thresholds


class RemoteController:
    def __init__(self, ring, presence, gate, source, stop_event, *, state_dir: Path | None,
                 calibration_config, leave_seconds=30.0, capture_seconds=30.0):
        self.ring, self.presence, self.gate, self.source = ring, presence, gate, source
        self.stop_event = stop_event
        self.state_file = state_dir / "presence.json" if state_dir else None
        self.calibration_config = calibration_config
        self.leave_seconds, self.capture_seconds = leave_seconds, capture_seconds
        self.lock = threading.Lock()
        if self.state_file and self.state_file.exists():
            if self.state_file.stat().st_size > 16384:
                raise ValueError("presence state file exceeds size limit")
            saved = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.presence.apply_settings(PresenceSettings.model_validate(saved["effective"]).model_dump())

    def _save_apply(self, cmd, values):
        effective = PresenceSettings.model_validate(values).model_dump()
        if cmd.expires_at is None or datetime.now(UTC) >= cmd.expires_at:
            raise TimeoutError("command expired before apply")
        if self.state_file:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state_file.with_suffix(".tmp")
            temp.write_text(json.dumps({"revision": str(cmd.cmd_id), "effective": effective}), encoding="utf-8")
            temp.replace(self.state_file)
        self.presence.apply_settings(effective)
        return effective

    def set_config(self, cmd, report):
        del report
        with self.lock:
            effective = self._save_apply(cmd, cmd.args)
            return {"revision": str(cmd.cmd_id), "effective": effective}

    def _wait(self, seconds, cmd, report, stage, offset):
        started = time.monotonic()
        while time.monotonic() - started < seconds:
            if self.stop_event.wait(min(1, max(.01, seconds / 10))):
                raise RuntimeError("edge stopped")
            if not self.source.running:
                raise RuntimeError("CSI disconnected")
            if cmd.expires_at is None or datetime.now(UTC) >= cmd.expires_at:
                raise TimeoutError("command expired")
            report(stage=stage, progress=min(.95, offset + .45 * (time.monotonic() - started) / seconds))

    def calibrate(self, cmd, report):
        request = CalibrationRequest.model_validate(cmd.args)
        if request.empty_room_confirmed is not True:
            raise ValueError("empty room confirmation required")
        with self.lock:
            self.gate.inhibit(True)
            try:
                report(stage="LEAVING", progress=0)
                self._wait(self.leave_seconds, cmd, report, "LEAVING", 0)
                before_count = self.ring.total_frames
                before_reset = self.ring.device_resets
                report(stage="MEASURING", progress=.45)
                self._wait(self.capture_seconds, cmd, report, "MEASURING", .45)
                window = self.ring.get_window(self.capture_seconds)
                if window is None or self.ring.device_resets != before_reset:
                    raise ValueError("fresh baseline missing or receiver reset")
                times, amps = window
                # No stale pre-capture buffer, no backwards timestamps, no silent
                # gaps. At least 95% of the configured baseline must be present.
                if (len(times) < max(100, self.capture_seconds * 50)
                        or len(times) > self.ring.total_frames - before_count + 2
                        or times[-1] - times[0] < self.capture_seconds * .95
                        or np.any(np.diff(times) <= 0) or np.max(np.diff(times)) > .05
                        or not np.all(np.isfinite(amps))):
                    raise ValueError("baseline coverage or continuity invalid")
                cfg = replace(self.presence.config)
                # Reuse the original model-independent baseline rules exactly.
                c = self.calibration_config
                mv, wander = _compute_baseline_thresholds((times * 1e6, amps), cfg,
                                                          c.k_mv, c.mv_floor, c.wander_baseline_floor, require_valid=True)
                values = {name: getattr(cfg, name) for name in PresenceSettings.model_fields}
                values.update(presence_mv_threshold=mv, wander_baseline=wander)
                effective = self._save_apply(cmd, values)
                return {"run_id": str(cmd.cmd_id), "effective": effective}
            finally:
                self.gate.inhibit(False)
