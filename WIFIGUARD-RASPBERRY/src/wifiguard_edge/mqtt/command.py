"""Allowlisted commands with deadline checks and a durable QoS1 dedup journal."""
from __future__ import annotations
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Callable
from uuid import UUID
from pydantic import ValidationError
from wifiguard_contracts import topics
from wifiguard_contracts.mqtt import AckMsg, AckStatus, CmdMsg
from . import codec
from .publisher import Outgoing

log = logging.getLogger("mqtt.command")
CommandHandlerFn = Callable[[CmdMsg, Callable[..., None]], dict[str, Any]]


class CommandHandler:
    def __init__(self, publisher: Any, *, device_id: UUID, tenant_id: str, qos: int = 1,
                 journal: Path | None = None, require_expiry: bool = False):
        self.publisher, self.device_id, self.tenant_id, self.qos = publisher, device_id, tenant_id, qos
        self.journal, self.require_expiry = journal, require_expiry
        self._handlers: dict[str, CommandHandlerFn] = {}
        self._seq = 0
        self._lock = threading.RLock()
        self._workers: list[threading.Thread] = []
        self._entries: dict[str, dict] = {}
        if journal and journal.exists():
            if journal.stat().st_size > 2_000_000:
                raise ValueError("command journal too large")
            saved = json.loads(journal.read_text(encoding="utf-8"))
            self._seq = int(saved["seq"])
            self._entries = saved["entries"]
            if len(self._entries) > 256:
                raise ValueError("command journal entry limit")
            for entry in self._entries.values():
                ack = AckMsg.model_validate(entry["ack"])
                if ack.status in ("accepted", "progress"):
                    # Never replay a partially executed action after a restart.
                    entry["ack"] = ack.model_copy(update={"status": "error", "detail": "edge restarted during command"}).model_dump(mode="json")

    def register(self, action: str, fn: CommandHandlerFn):
        self._handlers[action] = fn

    @property
    def cmd_topic(self):
        return topics.leaf_topic(self.tenant_id, self.device_id, "cmd")

    @property
    def ack_topic(self):
        return topics.leaf_topic(self.tenant_id, self.device_id, "ack")

    def attach(self, client):
        client.subscribe(self.cmd_topic, qos=self.qos)
        client.on_message = self._on_message

    def _persist(self):
        if self.journal:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            temp = self.journal.with_suffix(".tmp")
            with temp.open("w", encoding="utf-8") as stream:
                json.dump({"seq": self._seq, "entries": self._entries}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(self.journal)
            if os.name == "posix":
                directory = os.open(self.journal.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)

    def _on_message(self, client, userdata, message):
        try:
            self._handle_message(client, userdata, message)
        except Exception:
            # A failed journal write must neither execute the command nor kill
            # the MQTT network loop and its unrelated presence/telemetry traffic.
            log.exception("remote command rejected before durable execution")

    def _handle_message(self, client, userdata, message):
        del client, userdata
        try:
            cmd = CmdMsg.model_validate_json(message.payload)
        except ValidationError:
            log.warning("invalid remote command ignored")
            return
        if (message.topic != self.cmd_topic or cmd.device_id != self.device_id or cmd.tenant_id != self.tenant_id):
            return
        now = datetime.now(UTC)
        if (cmd.ts.tzinfo is None or cmd.ts > now + timedelta(seconds=5)
                or (cmd.expires_at is None and self.require_expiry)
                or (cmd.expires_at is not None and (cmd.expires_at.tzinfo is None
                    or not now < cmd.expires_at <= cmd.ts + timedelta(seconds=180)))):
            return  # stale/retained commands are never executed, including after reboot
        fingerprint = hashlib.sha256(cmd.model_dump_json().encode()).hexdigest()
        key = str(cmd.cmd_id)
        with self._lock:
            existing = self._entries.get(key)
            if existing:
                if existing["fingerprint"] == fingerprint:
                    ack = AckMsg.model_validate(existing["ack"])
                    self._send_ack(cmd.cmd_id, cmd.action, ack.status, stage=ack.stage,
                                   progress=ack.progress, detail=ack.detail, result=ack.result)
                return
            # Bounded disk/memory; unexpired records are never evicted to permit replay.
            self._entries = {k: v for k, v in self._entries.items() if v["expires"] > now.timestamp()}
            if len(self._entries) >= 256:
                return
            self._workers = [w for w in self._workers if w.is_alive()]
            self._entries[key] = {"fingerprint": fingerprint,
                                  "expires": (cmd.expires_at or now + timedelta(seconds=180)).timestamp()}
            handler = self._handlers.get(cmd.action)
            if handler is None or self._workers:
                self._send_ack(cmd.cmd_id, cmd.action, "error", detail="unsupported command or device busy")
                return
            # Persist ACCEPTED before a worker may apply any state.
            self._send_ack(cmd.cmd_id, cmd.action, "accepted")
            worker = threading.Thread(target=self._run, args=(cmd, handler), daemon=True, name=f"cmd-{cmd.action}")
            self._workers.append(worker)
            worker.start()

    def _run(self, cmd, handler):
        def report(status: AckStatus = "progress", **kw):
            self._send_ack(cmd.cmd_id, cmd.action, status, **kw)
        try:
            result = handler(cmd, report) or {}
        except Exception:
            log.exception("remote %s failed", cmd.action)
            self._send_ack(cmd.cmd_id, cmd.action, "error", detail="device action failed")
            return
        self._send_ack(cmd.cmd_id, cmd.action, "done", result=result)

    def _send_ack(self, cmd_id, action, status, **kw):
        with self._lock:
            self._seq += 1
            msg = codec.build_ack(device_id=self.device_id, tenant_id=self.tenant_id, seq=self._seq,
                                  cmd_id=cmd_id, action=action, status=status, **kw)
            self._entries[str(cmd_id)]["ack"] = msg.model_dump(mode="json")
            self._persist()
            self.publisher.enqueue(Outgoing(topic=self.ack_topic, payload=codec.to_bytes(msg), qos=self.qos, kind="ack"))


def make_ping_handler():
    def ping(cmd, report):
        del report
        return {"echo": cmd.args}
    return ping
