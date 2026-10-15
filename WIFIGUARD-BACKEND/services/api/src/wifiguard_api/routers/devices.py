from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, ValidationError

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wifiguard_contracts import topics
from wifiguard_contracts.api import DeviceIn, DeviceOut, DevicePatch, ErrorOut
from wifiguard_db.models import Device, ResidentDevice, EdgeCommand
from wifiguard_db.models.edge_command import ACTIVE_COMMAND_STATES
from wifiguard_contracts.control import CalibrationRequest, PresenceSettings

from ..deps import Scope, add_log, get_db, get_scope, scope_filter, scoped_get, stamp_scope
from ..errors import ApiError

router = APIRouter(prefix="/devices", tags=["devices"])
_ERR = {401: {"model": ErrorOut}, 404: {"model": ErrorOut}}


class RemoteCommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["calibrate", "set_config", "ping"]
    args: dict[str, Any] = {}


class RemoteCommandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    device_id: uuid.UUID
    action: str
    state: str
    created_at: datetime
    expires_at: datetime
    attempts: int
    stage: str | None
    progress: float | None
    result: dict | None
    error: str | None


def scope_tenant_id(scope: Scope) -> str:
    """스코프 → MQTT tenant 문자열. `presence_samples.facility_id` 에도 같은 값이 들어간다.

    분기 기준은 `is_facility` 다. `facility_id is None` 과 동치이긴 하나(users 테이블의
    service/role/facility_id CHECK 가 보장한다) 그 불변식에 기대지 않고 명시한다.
    """
    return topics.tenant_id(
        facility_id=scope.facility_id if scope.is_facility else None,
        user_id=scope.user_id,
    )


def issue_mqtt_topic(scope: Scope, device_id: uuid.UUID) -> str:
    """명세 backend §7.1 — wifiguard/{facility}/{device}. HOME 은 {facility} 자리에 home-{userId}.

    문자열 조립은 `wifiguard_contracts.topics` 가 유일한 출처다 — 엣지의 발행 토픽과
    브리지의 파싱이 같은 규칙을 따로 알고 있으면 조용히 갈라진다.
    """
    return topics.device_base(scope_tenant_id(scope), device_id)


def _validate_target(connection: str, mqtt_topic: str | None, serial_port: str | None) -> None:
    if connection == "SERIAL" and not serial_port:
        raise ApiError(422, "VALIDATION_ERROR", "입력값이 올바르지 않습니다.", {"serialPort": "SERIAL 연결은 포트가 필요합니다."})


def to_out(dev: Device) -> DeviceOut:
    """`online` 만 최신값 캐시로 덮는다. 나머지는 DB 행 그대로.

    DB 의 `devices.online` 은 NOT NULL 이라 "모름"을 담지 못한다. 텔레메트리를 한 번도
    받은 적 없는 기기를 `False`("연결 끊김")로 단정 표시하는 것이 지금까지의 문제였다.
    캐시에 항목이 없으면 **None** 을 내보내 프론트가 3상태로 구분하게 한다.

    캐시에 있으면 그 값이 DB 보다 정확하다 — 텔레메트리 싱크는 30초에 한 번만 쓰고
    그 사이 변화는 캐시에만 있기 때문이다(telemetry_sink.MIN_WRITE_INTERVAL_S).
    """
    out = DeviceOut.model_validate(dev)
    live = _live_state(dev.id)
    if live is None:
        out.online = None
        return out
    out.online = live.online(_offline_after_s())
    if live.last_seen_at is not None:
        out.last_seen_at = live.last_seen_at
    return out


def _live_state(device_id: uuid.UUID):
    try:
        from wifiguard_ingest.service import get_service
    except ImportError:
        return None
    service = get_service()
    return service.cache.get(device_id) if service else None


def _offline_after_s() -> float:
    """유예 임계값의 출처는 **LiveCache** 다 — 타임스탬프를 들고 있는 쪽이 소유해야 한다.

    settings 에도 같은 값이 있지만 그쪽은 캐시를 만들 때 쓰는 초기값이고, 런타임에 바꾸면
    두 값이 갈라진다. 읽을 때는 항상 캐시를 본다.
    """
    try:
        from wifiguard_ingest.service import get_service
    except ImportError:
        return 15.0
    service = get_service()
    return service.cache.offline_after_s if service else 15.0


@router.get("", response_model=list[DeviceOut], responses=_ERR)
def list_devices(scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> list[DeviceOut]:
    rows = db.execute(select(Device).where(scope_filter(Device, scope)).order_by(Device.created_at)).scalars()
    return [to_out(d) for d in rows]


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED,
             responses={**_ERR, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}})
def create_device(body: DeviceIn, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> DeviceOut:
    _validate_target(body.connection, body.mqtt_topic, body.serial_port)
    device_id = uuid.uuid4()
    topic = body.mqtt_topic.strip() if body.mqtt_topic else None
    if body.connection == "MQTT" and not topic:
        topic = issue_mqtt_topic(scope, device_id)
    dev = Device(
        id=device_id, name=body.name.strip(), room=body.room.strip(), connection=body.connection,
        mqtt_topic=topic, serial_port=body.serial_port, mac=body.mac, fw=body.fw,
    )
    stamp_scope(dev, scope)
    db.add(dev)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "TOPIC_TAKEN", "이미 사용 중인 MQTT 토픽입니다.") from exc
    add_log(db, scope, "INFO", f"장치 등록: {dev.name} ({dev.room}) · {dev.connection}", device_id=dev.id)
    db.commit()
    return to_out(dev)


@router.get("/{device_id}", response_model=DeviceOut, responses=_ERR)
def get_device(device_id: uuid.UUID, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> DeviceOut:
    return to_out(scoped_get(db, Device, device_id, scope, what="장치"))


@router.get("/{device_id}/commands", response_model=list[RemoteCommandOut], responses=_ERR)
def list_commands(device_id: uuid.UUID, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    scoped_get(db, Device, device_id, scope, what="장치")
    return list(db.execute(select(EdgeCommand).where(EdgeCommand.device_id == device_id)
                           .order_by(EdgeCommand.created_at.desc()).limit(20)).scalars())


@router.post("/{device_id}/commands", response_model=RemoteCommandOut, status_code=202,
             responses={**_ERR, 403: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}})
def queue_command(device_id: uuid.UUID, body: RemoteCommandIn,
                  scope: Scope = Depends(get_scope), db: Session = Depends(get_db)):
    dev = scoped_get(db, Device, device_id, scope, what="장치")
    if not scope.can_edit_config():
        raise ApiError(403, "FORBIDDEN", "장치 제어 권한이 없습니다.")
    if dev.connection != "MQTT" or dev.mqtt_topic != issue_mqtt_topic(scope, dev.id):
        raise ApiError(409, "REMOTE_DEVICE_REQUIRED", "이 계정에 발급된 MQTT 장치만 원격 제어할 수 있습니다.")
    try:
        if body.action == "calibrate":
            args = CalibrationRequest.model_validate(body.args).model_dump()
            if args["empty_room_confirmed"] is not True:
                raise ValueError("빈방 확인 필요")
        elif body.action == "set_config":
            args = PresenceSettings.model_validate(body.args).model_dump()
        else:
            if body.args:
                raise ValueError("ping takes no arguments")
            args = {}
    except (ValidationError, ValueError) as exc:
        raise ApiError(422, "INVALID_COMMAND_ARGS", "명령 인수를 확인하세요. 빈방 보정은 공간을 비운 뒤 확인해야 합니다.") from exc
    # Serialize requests for this device. The partial unique index also protects
    # against a concurrent API process and unanticipated writers.
    db.execute(select(Device.id).where(Device.id == dev.id).with_for_update()).one()
    active = db.execute(select(EdgeCommand.id).where(EdgeCommand.device_id == dev.id,
                           EdgeCommand.state.in_(ACTIVE_COMMAND_STATES))).first()
    if active:
        raise ApiError(409, "COMMAND_IN_PROGRESS", "이전 명령의 장치 응답을 기다리는 중입니다.")
    now = datetime.now(UTC)
    cmd = EdgeCommand(id=uuid.uuid4(), device_id=dev.id, actor_user_id=scope.user_id,
                      tenant_id=scope_tenant_id(scope), action=body.action, args=args,
                      state="PENDING", created_at=now,
                      expires_at=now + timedelta(seconds=180 if body.action == "calibrate" else 60),
                      next_attempt_at=now, attempts=0, last_ack_seq=-1)
    db.add(cmd)
    add_log(db, scope, "WARN", f"[{dev.name}] 원격 {body.action} 요청 · 장치 응답 대기 · {cmd.id}", device_id=dev.id)
    db.commit()
    return cmd


@router.patch("/{device_id}", response_model=DeviceOut, responses={**_ERR, 409: {"model": ErrorOut}})
def patch_device(device_id: uuid.UUID, body: DevicePatch, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> DeviceOut:
    dev = scoped_get(db, Device, device_id, scope, what="장치")
    data = body.model_dump(exclude_unset=True)
    if dev.connection in ("SPI", "MQTT") or data.get("connection") in ("SPI", "MQTT"):
        measured = {"calibrating", "calibration_stage", "calibration_progress", "presence_mv_threshold", "wander_baseline"}
        if measured.intersection(data):
            raise ApiError(409, "REAL_CALIBRATION_ONLY", "보정 결과는 실제 장치 실행·응답으로만 저장합니다.")
    if db.execute(select(EdgeCommand.id).where(EdgeCommand.device_id == dev.id,
                  EdgeCommand.state.in_(ACTIVE_COMMAND_STATES))).first():
        raise ApiError(409, "COMMAND_IN_PROGRESS", "장치 응답 대기 중에는 장치 설정을 바꿀 수 없습니다.")
    from .local_control import binding, enabled
    bound = binding() if enabled() else None
    if bound and bound["device_id"] == str(dev.id) and data.get("connection", "SPI") != "SPI":
        raise ApiError(409, "LOCAL_DEVICE_BOUND", "Pi5에 연결된 장치의 연결 방식을 바꿀 수 없습니다.")
    prev_stage = dev.calibration_stage
    previous_connection = dev.connection
    for key, value in data.items():
        setattr(dev, key, value.strip() if isinstance(value, str) else value)
    if previous_connection != dev.connection:
        # A legacy SERIAL calibration may not be relabelled as MQTT/SPI proof.
        dev.calibrating = False
        dev.calibration_stage = "IDLE"
        dev.calibration_progress = 0
        dev.presence_mv_threshold = None
        dev.wander_baseline = None
        dev.calibration_run_id = None
    _validate_target(dev.connection, dev.mqtt_topic, dev.serial_port)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "TOPIC_TAKEN", "이미 사용 중인 MQTT 토픽입니다.") from exc
    if prev_stage != "DONE" and dev.calibration_stage == "DONE":
        add_log(db, scope, "INFO",
                f"[{dev.name}] 캘리브레이션 완료 · 움직임 임계값={dev.presence_mv_threshold} 재실 baseline={dev.wander_baseline}",
                device_id=dev.id)
    elif prev_stage == "IDLE" and dev.calibration_stage == "LEAVING":
        add_log(db, scope, "WARN", f"[{dev.name}] 재설정 시작 · 공간을 비워주세요 (약 61초 소요)", device_id=dev.id)
    db.commit()
    return to_out(dev)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT, responses={**_ERR, 409: {"model": ErrorOut}})
def delete_device(device_id: uuid.UUID, scope: Scope = Depends(get_scope), db: Session = Depends(get_db)) -> Response:
    dev = scoped_get(db, Device, device_id, scope, what="장치")
    from .local_control import binding, enabled
    bound = binding() if enabled() else None
    if bound and bound["device_id"] == str(dev.id):
        raise ApiError(409, "LOCAL_DEVICE_BOUND", "Pi5에 다른 SPI 장치를 연결한 뒤 이 장치를 삭제하세요.")
    primary_of = list(db.execute(
        select(ResidentDevice.resident_id).where(ResidentDevice.device_id == dev.id, ResidentDevice.is_primary.is_(True))
    ).scalars())
    if primary_of:
        raise ApiError(409, "DEVICE_IS_PRIMARY", "거주자의 주 장치라 삭제할 수 없습니다. 먼저 주 장치를 바꾸세요.",
                       {"residentIds": [str(r) for r in primary_of]})
    add_log(db, scope, "WARN", f"장치 삭제: {dev.name} ({dev.room})")
    db.delete(dev)  # resident_devices 부 매핑은 CASCADE
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
