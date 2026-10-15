"""DB outbox → MQTT command; only matching, timely ACKs change applied state.

MQTT PUBACK is delivery to the broker, NOT execution by the device. Retrying
uses the same command ID; the receiver journals it before starting any work.
"""
from datetime import UTC, datetime, timedelta
import logging
import threading
from sqlalchemy import select
from wifiguard_contracts import topics
from wifiguard_contracts.control import PresenceSettings
from wifiguard_contracts.mqtt import AckMsg, CmdMsg
from wifiguard_db.engine import get_session
from wifiguard_db.models import Device, EdgeCommand, EventLog
from wifiguard_db.models.edge_command import ACTIVE_COMMAND_STATES

log = logging.getLogger("ingest.remote_control")


def audit(db, cmd, dev, message):
    db.add(EventLog(level="INFO" if cmd.state == "DONE" else "WARN", msg=message,
                    actor_user_id=cmd.actor_user_id, device_id=dev.id,
                    facility_id=dev.facility_id, owner_user_id=dev.owner_user_id))


def expire_command(db, cmd, dev):
    cmd.state = "EXPIRED"
    cmd.error = "장치 완료 응답 기한이 지났습니다. 적용 여부를 확인한 후 다시 요청하세요."
    if cmd.action == "calibrate":
        dev.calibrating = False
        dev.calibration_stage = "ERROR"
    audit(db, cmd, dev, f"[{dev.name}] 원격 {cmd.action} 응답 만료 · {cmd.id}")


def apply_ack(msg: AckMsg, *, session_factory=get_session, now=None) -> bool:
    now = now or datetime.now(UTC)
    with session_factory() as db:
        cmd = db.execute(select(EdgeCommand).where(EdgeCommand.id == msg.cmd_id).with_for_update()).scalar_one_or_none()
        if cmd is None or cmd.state not in ACTIVE_COMMAND_STATES:
            return False
        dev = db.get(Device, cmd.device_id)
        if (dev is None or cmd.device_id != msg.device_id or cmd.tenant_id != msg.tenant_id
                or cmd.action != msg.action or msg.seq <= cmd.last_ack_seq):
            return False
        # Reject stale replay, future clock claims and ACKs for an expired run.
        if msg.ts.tzinfo is None or not cmd.created_at - timedelta(seconds=5) <= msg.ts <= now + timedelta(seconds=5):
            return False
        if now >= cmd.expires_at:
            expire_command(db, cmd, dev)
            return False
        if cmd.state == "PROGRESS" and msg.status == "accepted":
            return False
        result = None
        if msg.status == "done":
            try:
                if msg.action == "calibrate":
                    if msg.result.get("run_id") != str(cmd.id):
                        return False
                    result = PresenceSettings.model_validate(msg.result["effective"]).model_dump()
                elif msg.action == "set_config":
                    result = PresenceSettings.model_validate(msg.result["effective"]).model_dump()
                    if msg.result.get("revision") != str(cmd.id) or result != cmd.args:
                        return False
                else:
                    result = {}
            except (ValueError, KeyError, TypeError):
                return False
        cmd.last_ack_seq = msg.seq
        cmd.state = msg.status.upper()
        cmd.stage = msg.stage if msg.stage in ("LEAVING", "MEASURING", "DONE", "ERROR") else None
        cmd.progress = max(cmd.progress or 0.0, msg.progress or 0.0)
        if msg.status == "error":
            # Do not store arbitrary device tracebacks, tokens, or personal data.
            cmd.error = "장치가 명령을 완료하지 못했습니다. 수신 연결·빈방 상태·설정을 확인하세요."
        if result is not None:
            cmd.result = {"effective": result} if msg.action != "ping" else {}
            cmd.progress = 1.0
            if msg.action != "ping":
                dev.presence_mv_threshold = result["presence_mv_threshold"]
                dev.wander_baseline = result["wander_baseline"]
        if cmd.action == "calibrate":
            dev.calibrating = msg.status in ("accepted", "progress")
            dev.calibration_stage = ("DONE" if msg.status == "done" else "ERROR" if msg.status == "error"
                                     else cmd.stage or "WAITING_ACK")
            dev.calibration_progress = cmd.progress or 0.0
            if msg.status == "done":
                dev.calibration_run_id = str(cmd.id)
        if msg.status in ("done", "error"):
            audit(db, cmd, dev, f"[{dev.name}] 원격 {cmd.action} {cmd.state} · {cmd.id}")
        return True


class CommandDispatcher:
    def __init__(self, bridge, *, session_factory=get_session):
        self.bridge = bridge
        self.sessions = session_factory
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name="edge-command-outbox")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self):
        while not self.stop_event.wait(1):
            try:
                self.tick()
            except Exception:
                log.exception("remote command dispatcher tick failed; outbox retained")

    def tick(self, now=None):
        now = now or datetime.now(UTC)
        outgoing = []
        with self.sessions() as db:
            rows = db.execute(select(EdgeCommand).where(
                EdgeCommand.state.in_(ACTIVE_COMMAND_STATES),
                EdgeCommand.next_attempt_at <= now,
            ).order_by(EdgeCommand.created_at).limit(20).with_for_update(skip_locked=True)).scalars()
            for cmd in rows:
                dev = db.get(Device, cmd.device_id)
                if dev is None:
                    continue
                if now >= cmd.expires_at:
                    expire_command(db, cmd, dev)
                    continue
                if cmd.attempts >= 6:
                    cmd.next_attempt_at = cmd.expires_at
                    continue
                cmd.attempts += 1
                cmd.next_attempt_at = min(now + timedelta(seconds=15), cmd.expires_at)
                if cmd.state == "PENDING":
                    cmd.state = "SENT"
                payload = CmdMsg(device_id=cmd.device_id, tenant_id=cmd.tenant_id,
                                 ts=cmd.created_at, expires_at=cmd.expires_at, cmd_id=cmd.id,
                                 action=cmd.action, args=cmd.args).model_dump_json().encode()
                outgoing.append((topics.leaf_topic(cmd.tenant_id, cmd.device_id, "cmd"), payload))
        # Commit BEFORE publishing: a fast device ACK must not be overwritten by SENT.
        for topic, payload in outgoing:
            try:
                self.bridge.publish_command(topic, payload)
            except Exception:
                log.warning("remote command publish failed; durable retry scheduled")
