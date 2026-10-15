from datetime import UTC, datetime, timedelta
import json
import threading
from types import SimpleNamespace
from uuid import uuid4
import pytest
from wifiguard_contracts.mqtt import CmdMsg
from wifiguard_edge.mqtt.command import CommandHandler
from wifiguard_edge.remote_control import RemoteController
from wifiguard_edge.presence_loop import PresenceLoop
from wifiguard_edge.presence import PresenceConfig
from wifiguard_edge.csi.buffer import RingBuffer
from wifiguard_edge.config import CalibrationConfig
from wifiguard_edge.gating import SignalGate

VALUES = {"presence_mv_threshold": .7, "wander_baseline": .12,
          "wander_ratio_threshold": 1.8, "presence_timeout_s": 12}


class Publisher:
    def __init__(self): self.messages = []
    def enqueue(self, outgoing): self.messages.append(json.loads(outgoing.payload))


def command(device, **kwargs):
    now = datetime.now(UTC)
    fields = dict(device_id=device, tenant_id="tenant", ts=now, expires_at=now + timedelta(seconds=120),
                  cmd_id=uuid4(), action="set_config", args=VALUES)
    fields.update(kwargs)
    return CmdMsg(**fields)


def deliver(handler, cmd):
    handler._on_message(None, None, SimpleNamespace(topic=handler.cmd_topic, payload=cmd.model_dump_json().encode()))
    for worker in handler._workers: worker.join(timeout=2)


def test_durable_duplicate_command_does_not_reapply(tmp_path):
    publisher = Publisher()
    device = uuid4()
    journal = tmp_path / "journal.json"
    calls = []
    def callback(cmd, report):
        calls.append(cmd.cmd_id)
        return {"revision": str(cmd.cmd_id), "effective": VALUES}
    cmd = command(device)
    for _ in range(2):
        handler = CommandHandler(publisher, device_id=device, tenant_id="tenant", journal=journal, require_expiry=True)
        handler.register("set_config", callback)
        deliver(handler, cmd)
    assert calls == [cmd.cmd_id]
    assert publisher.messages[-1]["status"] == "done"


@pytest.mark.parametrize("change", ["tenant", "device", "expired", "missing"])
def test_wrong_identity_or_expired_commands_are_not_executed(change):
    publisher, device = Publisher(), uuid4()
    handler = CommandHandler(publisher, device_id=device, tenant_id="tenant", require_expiry=True)
    handler.register("set_config", lambda *args: pytest.fail("must not execute"))
    values = {"tenant": {"tenant_id": "other"}, "device": {"device_id": uuid4()},
              "expired": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}, "missing": {"expires_at": None}}
    deliver(handler, command(device, **values[change]))
    assert publisher.messages == []


def controller(tmp_path):
    ring = RingBuffer()
    presence = PresenceLoop(ring, PresenceConfig())
    return RemoteController(ring, presence, SignalGate(), SimpleNamespace(running=True), threading.Event(),
                            state_dir=tmp_path, calibration_config=CalibrationConfig(), leave_seconds=.01, capture_seconds=.01)


def test_configuration_persisted_and_reloaded(tmp_path):
    control = controller(tmp_path)
    cmd = command(uuid4())
    result = control.set_config(cmd, lambda **kwargs: None)
    assert result["revision"] == str(cmd.cmd_id)
    assert controller(tmp_path).presence.config.presence_mv_threshold == .7
    with pytest.raises(ValueError):
        control.set_config(command(cmd.device_id, args={**VALUES, "presence_timeout_s": -1}), None)


def test_journal_failure_is_fail_closed_without_killing_callback(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("owned fixture")
    device = uuid4()
    publisher = Publisher()
    calls = []
    handler = CommandHandler(publisher, device_id=device, tenant_id="tenant",
                             journal=blocked / "commands.json", require_expiry=True)
    handler.register("set_config", lambda *args: calls.append(1))
    deliver(handler, command(device))
    assert calls == [] and publisher.messages == []


def test_missing_baseline_never_succeeds_or_leaves_gate_inhibited(tmp_path):
    control = controller(tmp_path)
    control.gate.note_presence("present")
    cmd = command(uuid4(), action="calibrate", args={"empty_room_confirmed": True})
    reports = []
    with pytest.raises(ValueError, match="baseline"):
        control.calibrate(cmd, lambda **kwargs: reports.append(kwargs))
    assert control.gate.is_open
    assert not (tmp_path / "presence.json").exists()
    assert {r["stage"] for r in reports} == {"LEAVING", "MEASURING"}
