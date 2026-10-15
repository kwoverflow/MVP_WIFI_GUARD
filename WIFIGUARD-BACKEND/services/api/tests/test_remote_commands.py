from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select
from helpers import create_device, signup_home, signup_root, signup_member, invite_code
from wifiguard_contracts.mqtt import AckMsg
from wifiguard_db.models import Device, EdgeCommand
from wifiguard_ingest.remote_control import CommandDispatcher, apply_ack

SETTINGS = {"presence_mv_threshold": .8, "wander_baseline": .1,
            "wander_ratio_threshold": 1.8, "presence_timeout_s": 10}


def setup(client):
    actor = signup_home(client, "remote@example.test")
    device = create_device(client, actor)
    url = f"/api/v1/devices/{device['id']}/commands"
    return actor, device, url


def request(client, actor, url, action="set_config", args=None):
    response = client.post(url, headers=actor.h, json={"action": action, "args": SETTINGS if args is None else args})
    assert response.status_code == 202, response.text
    return response.json()


def ack(cmd, device, actor, *, status="done", seq=1, **extra):
    return AckMsg(device_id=device["id"], tenant_id=f"home-{actor.user['id']}",
                  ts=datetime.now(UTC), seq=seq, cmd_id=cmd["id"], action=cmd["action"], status=status,
                  result={"revision": cmd["id"], "effective": SETTINGS}, **extra)


def test_remote_settings_ack_only_applies_after_matching_done(client, db):
    actor, device, url = setup(client)
    cmd = request(client, actor, url)
    assert cmd["state"] == "PENDING" and cmd["result"] is None
    dispatcher = CommandDispatcher(type("Bridge", (), {"publish_command": lambda self, topic, payload: None})())
    dispatcher.tick()
    assert client.get(url, headers=actor.h).json()[0]["state"] == "SENT"
    assert apply_ack(ack(cmd, device, actor, status="accepted"))
    assert apply_ack(ack(cmd, device, actor, status="done", seq=2))
    assert not apply_ack(ack(cmd, device, actor, status="done", seq=2))
    row = client.get(f"/api/v1/devices/{device['id']}", headers=actor.h).json()
    assert row["presenceMvThreshold"] == .8
    assert client.get(url, headers=actor.h).json()[0]["state"] == "DONE"


@pytest.mark.parametrize("change", ["device", "tenant", "time", "revision", "result"])
def test_wrong_stale_or_forged_ack_does_not_apply(client, change):
    actor, device, url = setup(client)
    cmd = request(client, actor, url)
    message = ack(cmd, device, actor)
    updates = {"device": {"device_id": uuid4()}, "tenant": {"tenant_id": "other"},
               "time": {"ts": datetime.now(UTC) - timedelta(days=1)},
               "revision": {"result": {"revision": str(uuid4()), "effective": SETTINGS}},
               "result": {"result": {"revision": cmd["id"], "effective": {**SETTINGS, "wander_baseline": float("nan")}}}}
    assert not apply_ack(message.model_copy(update=updates[change]))
    assert client.get(url, headers=actor.h).json()[0]["state"] == "PENDING"


def test_remote_expiry_and_bounded_retry(client, db):
    actor, device, url = setup(client)
    cmd = request(client, actor, url)
    now = datetime.now(UTC) + timedelta(minutes=2)
    bridge = type("Bridge", (), {"publish_command": lambda *args: pytest.fail("expired command published")})()
    CommandDispatcher(bridge).tick(now)
    assert client.get(url, headers=actor.h).json()[0]["state"] == "EXPIRED"
    assert not apply_ack(ack(cmd, device, actor))


def test_same_id_outbox_retries_are_bounded(client, db):
    actor, device, url = setup(client)
    cmd = request(client, actor, url, "calibrate", {"empty_room_confirmed": True})
    sent = []
    bridge = type("Bridge", (), {"publish_command": lambda self, topic, payload: sent.append(payload)})()
    dispatcher = CommandDispatcher(bridge)
    start = datetime.now(UTC)
    for index in range(12):
        dispatcher.tick(start + timedelta(seconds=index * 15))
    assert len(sent) == 6 and len(set(sent)) == 1
    assert client.get(url, headers=actor.h).json()[0]["attempts"] == 6


def test_confirmation_busy_and_no_manual_mqtt_calibration(client):
    actor, device, url = setup(client)
    bad = client.post(url, headers=actor.h, json={"action": "calibrate", "args": {"empty_room_confirmed": False}})
    assert bad.status_code == 422
    cmd = request(client, actor, url, "calibrate", {"empty_room_confirmed": True})
    assert client.post(url, headers=actor.h, json={"action": "ping"}).status_code == 409
    assert client.patch(f"/api/v1/devices/{device['id']}", headers=actor.h,
                        json={"calibrationStage": "DONE"}).status_code == 409
    message = ack(cmd, device, actor).model_copy(update={"result": {"run_id": cmd["id"], "effective": SETTINGS}})
    assert apply_ack(message)
    assert client.get(f"/api/v1/devices/{device['id']}", headers=actor.h).json()["calibrationStage"] == "DONE"


def test_command_permissions_scope_and_allowlist(client):
    root = signup_root(client, "root@control.test")
    member = signup_member(client, "member@control.test", invite_code(client, root))
    outsider = signup_home(client, "outsider@control.test")
    device = create_device(client, root)
    url = f"/api/v1/devices/{device['id']}/commands"
    assert client.post(url, headers=member.h, json={"action": "ping"}).status_code == 403
    assert client.get(url, headers=outsider.h).status_code == 404
    assert client.post(url, headers=root.h, json={"action": "shell", "args": {}}).status_code == 422
    assert client.post(url, headers=root.h, json={"action": "set_config", "args": {**SETTINGS, "path": "/etc"}}).status_code == 422
    assert client.post(url, headers=root.h, json={"action": "set_config", "args": {**SETTINGS, "wander_baseline": 0}}).status_code == 422
