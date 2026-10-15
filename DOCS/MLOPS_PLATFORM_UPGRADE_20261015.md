# WIFI GUARD 클라우드 관제 고도화와 배포 안내

기존 Raspberry Pi 수집 노드와 MQTT·Kafka·클라우드 추론 구조를 유지하면서, Pi5에서 다듬은 PC·모바일 웹 UI와 알림 기능을 연결한 버전이다. Pi5 단독 배포본을 클라우드 스택으로 바꾸는 작업이 아니라, 두 운영 방식의 사용자 경험과 서비스 계약을 맞춘 작업이다.

개인 포크 `kwoverflow/MVP_WIFI_GUARD`의 `main`에 제안한다. 팀 저장소 PR #2와 별도이며 자동 머지하지 않는다. 개인 포크의 기존 `main`이 이전 모델 통합보다 앞선 상태이므로, 비교 화면에는 이전 Pi3·segmentation·MLOps 기반 변경도 함께 포함된다.

## 구성과 책임

```text
ESP32 CSI → Raspberry Pi 수집·재실 감지·신호 창 생성
                         ↓ MQTT
                  브리지 → Kafka
                     ├→ 재실·장치 상태 → TimescaleDB / 최신 상태 캐시
                     └→ 피처 추출 → 실제 체크포인트 추론 → 낙상 결과 Kafka
                                                           ↓
                    WebSocket / REST ← PostgreSQL 낙상 이력·응답
                                                           ├→ SMTP 이메일
                                                           └→ FCM Android 푸시

웹 장치 제어 → PostgreSQL 명령 대기열 → MQTT cmd → 장치 실행
                                                  ↓ ack
                               Kafka → 인제스트 → 적용 상태·감사 이력

검토된 학습 데이터 → 별도 학습 / 파인튜닝 실행 → MLflow 버전 등록
                                                    ↓ 수동 검토·승인
                                                운영 모델 교체
```

재실 감지는 수집 노드에서 모델 추론과 독립적으로 돈다. 클라우드 피처·모델은 기존 `SignalMsg`의 3초·30개 입력 채널 계약을 받는다. Pi5 단독 운영에서 쓰는 0.5초 stride, 시작 보정 제어, 로컬 HTTP ingest와 systemd 묶음은 이 배포의 기본값이 아니다. 클라우드 빌드에서는 Pi5 수신 시작·정지 버튼과 로컬 제어 경로를 비활성화한다.

CPU 피처의 `FEATURE_BACKEND=cpu-optimized`는 기존 resample·Q-value·PCA·CWT·ACF 수식을 유지하면서 Pi5에서 사용한 상수 캐시와 최적화 커널을 재사용하는 선택 항목이다. 기본값 `reference`와 GPU 경로는 유지한다. 이 PR은 클라우드 모델 입력의 서브캐리어 선정 정책을 임의로 Pi5 정책으로 교체하지 않는다. 운영 입력을 상위 Q-value 30개로 바꿀 때에는 송수신 계약과 학습 입력 분포를 함께 검토해야 한다.

## 사용자 화면

빨간 방패 로고와 공통 디자인을 적용하고, 작은 화면에서는 카드·하단 메뉴를 사용한다. 장치·대상자·재실·움직임·낙상 이력·응답·알림 설정·계정·감지 설정을 실제 API에 연결한다. 연결이 끊기면 재실을 퇴실로 단정하지 않고 판단 불가로 표시한다. 이미 확인한 낙상은 재접속 때 DB의 처리 상태를 확인해 재알림하지 않는다. 다른 화면의 확인 처리도 현재 알람창에 반영한다.

`/train`은 저장된 학습·파인튜닝·등록 산출물과 현재 MLflow 연결 상태를 구분한다. 상태 확인 버튼은 학습 실행이나 모델 배포 버튼이 아니다. 실제 자동 드리프트 감지→재학습 DAG→무인 운영 배포는 연결되지 않았으며, 화면에도 별도 구성 필요로 표시한다. 이전 문서의 Airflow·Evidently 다이어그램은 목표 구조이지 이 버전의 완료 범위가 아니다.

`/system`과 `/train`은 인증된 자원 API를 사용한다. 수집하지 못한 CPU·RAM·디스크·GPU를 0으로 표시하지 않는다. 호스트 스냅샷은 10초가 지나면 갱신 끊김으로 전환한다.

## 원격 보정과 감지 설정

`POST /api/v1/devices/{id}/commands`에 `calibrate`, `set_config`, `ping`을 요청한다. 같은 경로의 GET은 최근 20개 명령 상태를 반환한다. HOME USER 또는 시설 ROOT만 요청할 수 있고 MEMBER는 조회만 가능하다. 다른 테넌트 장치는 404이며, 서버가 해당 계정에 발급한 정규 MQTT 토픽의 장치만 제어할 수 있다.

보정 요청에는 `{"action":"calibrate","args":{"empty_room_confirmed":true}}`가 필요하다. 퇴실 대기 30초 후 새 CSI를 30초 측정한다. 장치 재부팅, 부족한 측정 범위, 50ms 초과 샘플 간격, 비정상 수치 또는 MV/Welch 산출 실패를 만나면 완료하지 않는다. 기존 IQR·평균·표준편차 및 Welch baseline 규칙을 사용한다. 이것은 호스트 빈방 기준값 보정이며 SPI에 UART 전용 `train` 명령을 보내거나 펌웨어 AGC 보정을 했다고 주장하지 않는다.

보정 중에는 CSI 수집·재실 관측·텔레메트리를 유지하고 새 낙상 추론 신호 전송을 일시 중지한다. 이미 브로커에 전송된 이전 신호까지 취소하는 것은 아니다. 끝나면 기존 게이트 정책으로 복귀한다. 보정 실패는 이전 기준값을 새 성공 결과로 덮지 않는다.

감지 설정은 다음 네 값만 받는다. 모델 구조·낙상 확률 임계값·임의 파일·명령줄은 변경할 수 없다.

| 항목 | 허용 범위 |
|---|---|
| `presence_mv_threshold` | 0 초과, 100000 이하 |
| `wander_baseline` | 0 초과, 100000 이하 |
| `wander_ratio_threshold` | 1 이상, 100 이하 |
| `presence_timeout_s` | 1초 이상, 3600초 이하 |

요청은 DB에 먼저 기록한다. MQTT 발행 성공은 완료가 아니다. `PENDING → SENT → ACCEPTED → PROGRESS → DONE` 중 접수·진행·완료는 장치 ACK로만 갱신한다. 설정 완료에는 요청 ID와 같은 revision 및 실제 적용값이, 보정 완료에는 같은 run ID와 유효한 측정값이 있어야 한다. 다른 장치·테넌트·명령의 ACK, 중복·역순 ACK, 오래된 시각, 만료된 ACK는 적용하지 않는다. MQTT 장치의 보정 결과를 일반 PATCH로 위조할 수 없다.

장치당 한 번에 명령 하나만 실행한다. 전송은 동일 ID로 15초 간격 최대 6회 시도한다. 설정·ping의 만료는 60초, 보정은 180초다. 장치도 만료와 신원을 확인하며 실행 전 기록을 원자적으로 저장한다. QoS1 중복이나 재시작 후 같은 명령이 와도 보정을 다시 수행하지 않는다. 실행 도중 재시작한 명령은 실패 응답으로 남긴다. 실패·만료가 이미 적용된 설정의 원복을 뜻하지는 않으므로, 상태가 불확실하면 현장을 확인하고 새 요청을 보낸다.

`EDGE_CONTROL_STATE_DIR` 아래 장치 UUID별로 기준값과 중복 방지 기록을 보관한다. 제공 systemd unit은 `/var/lib/wifiguard-edge`를 모드 0700으로 만든다. Docker 수집 노드는 이 경로를 쓰기 가능한 영속 볼륨에 연결해야 한다. 여러 프로세스가 같은 장치의 상태 디렉터리를 동시에 사용하면 안 된다. NTP 시각 동기화와 브로커 장치별 ACL도 필요하다.

기존 클라우드 게이트의 기본 unknown 허용과 퇴실 후 10초 유예는 그대로다. Pi5의 별도 로컬 게이트 정책과 같다고 해석하면 안 된다.

## 알림 연결

이메일은 백엔드 SMTP 어댑터가 보내고 Android 푸시는 FCM HTTP v1로 보낸다. Firebase가 이메일까지 발송하는 구조가 아니다. 기본 개발 구성은 Mailpit에만 이메일을 적재하고 FCM을 끈다.

FCM은 별도 overlay로 활성화한다. Firebase 서버 서비스 계정 JSON은 체크아웃 외부의 보호된 파일로 두고 읽기 전용으로 마운트한다. APK와 Firebase 프로젝트가 일치해야 하며 앱에서 로그인·알림 허용·기기 등록을 마쳐야 한다. 서비스 계정 키를 앱, Git, Docker 이미지에 넣지 않는다. 푸시 outbox는 재시도·만료를 기록한다. FCM 접수, 휴대폰 수신, 알림 표시, 담당자 ACK는 각각 다르다.

휴대폰이 다른 네트워크에 있어도 FCM 푸시는 인터넷을 통해 수신할 수 있다. 앱의 로그인·이력 조회·응답에는 별도로 접근 가능한 HTTPS API 또는 VPN이 필요하다. 개발 HTTP 서버나 무인증 브로커를 인터넷에 공개하는 방식은 배포 방법이 아니다.

## 로컬 배포 절차

아래 명령은 저장소의 `WIFIGUARD-BACKEND` 디렉터리 기준이다. Python 3.12, Docker Compose v2, 실제 모델 체크포인트가 필요하다. 웹 단독 개발·빌드는 Node 24와 npm lockfile을 사용한다. 기존 Bun 개발 환경은 별도 경로로 유지한다.

```sh
python tools/prepare_cloud_env.py --checkpoint /absolute/protected/model.pt
docker compose --env-file compose/.env -f compose/docker-compose.dev.yml -f compose/docker-compose.ui.yml --profile model up -d --build
```

환경 생성기는 새로운 `.env`만 만들며 기존 파일을 덮지 않는다. DB·JWT·MinIO·Grafana 키를 무작위로 생성하고 값을 출력하지 않는다. 운영에서 기존 DB 비밀번호를 임의로 재생성하면 접속할 수 없으므로 업데이트 시에는 기존 비밀값을 유지한다. 체크포인트는 저장소 밖 파일을 읽기 전용으로 마운트한다.

웹은 기본 `http://127.0.0.1:8080`, API는 `http://127.0.0.1:8000/health`다. `/api`와 `/ws`를 같은 gateway로 전달하므로 CORS를 전체 허용할 필요가 없다. 클라우드 gateway는 `/internal` 및 Pi5 제어 경로를 차단한다. API는 인제스트·WS 캐시를 공유하는 **worker 1개**로 운영한다. 다중 API 인스턴스의 팬아웃·공유 캐시는 별도 설계가 필요하다.

이 개발 Compose의 공개 포트는 기본 loopback이다. 익명 Mosquitto 설정은 PC 재생 검증용이므로 실제 장비를 연결하려고 포트 바인딩만 외부로 열어서는 안 된다. 운영 broker는 TLS·장치별 인증과 ACL을 별도로 설정한다. 장치는 자기 `presence/signal/telemetry/ack`에만 발행하고 자기 `cmd`만 구독해야 한다. 백엔드 계정은 그 반대 권한을 갖는다. 외부 broker에는 `MQTT_HOST`, `MQTT_PORT`, `MQTT_TLS`, `MQTT_USERNAME`, `MQTT_PASSWORD`로 연결한다. AWS IoT Core의 클라이언트 인증서 기반 연결은 전용 인증 설정을 추가 검증해야 한다.

실제 FCM을 사용할 때에는 보호된 `.env`에 project ID와 외부 키 파일 경로를 추가한 뒤 아래 overlay까지 포함한다. 이 동작은 이후 실제 낙상 이메일·푸시를 발송할 수 있으므로 수신자와 운영 환경을 확인하고 실행한다.

```sh
docker compose --env-file compose/.env -f compose/docker-compose.dev.yml -f compose/docker-compose.ui.yml -f compose/docker-compose.fcm.yml --profile model up -d --build
```

실제 이메일은 운영 SMTP 호스트·포트·계정·앱 비밀번호와 `SMTP_STARTTLS=1`을 설정한다. Gmail 앱 비밀번호나 FCM 키를 문서·화면 캡처·PR에 넣지 않는다. LAN 웹을 열 때는 `WEB_HOST_BIND`를 설치 서버의 특정 LAN IP로 지정하고 방화벽·HTTPS·허용 사용자 범위를 검토한다.

## 자원 관측과 시간의 의미

호스트에서 다음 수집기를 별도 실행하면 인증된 UI가 최신 스냅샷을 읽는다. 수집기 HTTP는 loopback에만 두며 그 주소를 외부에 공개할 필요가 없다. 파일 하나를 원자적으로 갱신하고, 무제한 이력을 쌓지 않는다.

```sh
uv run --with psutil python tools/resource_monitor.py --host 127.0.0.1 --port 9108 --interval 2 --snapshot artifacts/runtime-resources/host.json
```

수집기는 `psutil`이 필요하다. OS 전체 CPU·RAM·해당 파일시스템 사용량이므로 다른 프로그램도 포함한다. 이것을 파이프라인 전용 총사용량으로 해석하지 않는다. 프로세스·컨테이너별 관측은 기존 `docker-compose.obs.yml`의 Grafana/cAdvisor와 함께 확인한다. GPU는 호스트의 `nvidia-smi` 관측이 가능한 경우에만 표시하며, 모델이 CPU에서 돌면 GPU 수치가 있다는 이유만으로 추론 GPU 사용량이라고 해석하지 않는다.

추론 worker의 스냅샷은 최근 최대 128개 처리 그룹의 윈도우당 배치 평균을 기록한다. features·inference·total의 중앙값·P95·최댓값과 수신·발행·오류 수를 제공한다. RAW 전처리, 수집 대기, 네트워크, Kafka 대기, 후처리 판정, SMTP/FCM 수신 지연은 이 처리시간에 포함되지 않는다. 짧은 합성 입력 검사 수치를 장시간 운용 성능 또는 실제 알람 지연으로 보장하지 않는다. CPU·GPU inference profile을 동시에 같은 Kafka group과 상태 볼륨으로 기동하지 않는다.

## 검증 결과

요약 수치와 제외 범위는 [검증 JSON](MLOPS_PLATFORM_VALIDATION_20261015.json)에도 기록했다. 개인 fork의 기존 main이 `0a3358a`여서 이 PR에는 이전 `5ce847f`까지의 Pi3/클라우드 기준선도 함께 들어간다. 팀 저장소 PR #2는 변경하지 않는다.

공개 배포 트리에서는 이전 참조용 NPZ 창과 개별 기록 CSV를 제외했고 로컬 파일은 보존했다. 기존 공개 Git 이력을 재작성하거나 삭제하지 않았으므로 과거 커밋의 데이터까지 제거됐다는 뜻은 아니다. 기준 데이터 평가에는 승인된 자료를 별도로 제공해야 하며 이번 합성 소프트웨어 E2E에는 필요하지 않다.

2026년 10월 15일, 기존 운영 스택과 분리한 임시 Docker 프로젝트에서 확인했다. DB·브로커·Mailpit·MLflow와 실제 체크포인트를 사용했다. 실물 Pi·ESP32에는 접속하거나 명령을 보내지 않았다.

| 검사 | 결과와 범위 |
|---|---|
| 백엔드 회귀 | 189 통과, 실패·skip 0. 원격 제어 역할·테넌트·만료·위조 ACK·보정 결과 보호 포함 |
| inference 회귀 | 13 통과. 합성 비정형 신호에서 reference와 CPU 최적화 피처 일치 `rtol=1e-5`, `atol=1e-6` 포함 |
| Raspberry 회귀 | 118 통과. 영속 중복 방지, 만료·신원 거부, 설정 재로딩, 부족한 보정 실패 포함 |
| 웹 회귀 | 95 통과. 타입 검사, 클라우드 production 빌드, PC·390px 모바일 화면 확인 |
| 실제 모델 소프트웨어 E2E | 합성 진폭창 → MQTT → Kafka → 실제 피처·체크포인트 → 결과 Kafka 통과 |
| 낙상 서비스 | 별도 양성 결과 fixture → DB 이력 1건·중복 방지 → ACKNOWLEDGED → Mailpit 수신 통과 |
| 재실 서비스 | MQTT 재실 → REST 상태 → 실제 TimescaleDB 시계열 행 적재 통과 |
| 원격 제어 | 실제 cmd/ack 계약으로 ping·설정 적용·30초+30초 빈방 기준값 계산·DB 반영 통과 |
| 학습과 등록 | 합성 피처 2개, encoder 고정, 학습·파인튜닝 각 1 batch의 실제 역전파·저장·MLflow 등록 통과 |

학습 fixture는 실행 경로 확인용이다. 합성 양성 결과 주입은 모델의 실제 낙상 판정을 증명하지 않는다. 최종 배포 승인은 실장비 수신, 빈방 보정의 현장 타당성, 실제 낙상·비낙상 정확도, 장시간 부하·저장 보존, 운영 외부 이메일·휴대폰 전달 검사를 거쳐야 한다.

## 업데이트와 복구

업데이트 전에 관계형 DB·TimescaleDB·MLflow 아티팩트·장치 상태 디렉터리와 기존 `.env`를 백업한다. API 기동 시 Alembic을 `0006`으로 올리며 `0004`·`0005`·`0006`은 기존 데이터를 삭제하지 않는 추가 migration이다. `downgrade base`는 테스트 전용이며 운영 절차가 아니다.

원격 명령이 진행 중이면 장치 설정·토픽을 바꾸지 않는다. 새 장치 에이전트를 먼저 배포해 같은 계약 버전을 맞춘 후 UI 명령을 사용한다. 구형 에이전트의 지원되지 않는 명령을 완료로 간주하지 않는다. 장애 때는 발송 상태·명령 ID·ACK·장치 로그·DB 연결을 확인한다. FCM이나 SMTP가 실패해도 앱 내 낙상 이력과 담당자 확인은 별도로 유지된다.

중지에는 동일한 Compose 파일 집합의 `stop`을 사용한다. 운영에서 `down --volumes`, Docker prune, DB 초기화를 복구 절차로 사용하지 않는다. 학습 모델 등록과 운영 체크포인트 교체도 분리하고, 모델 교체 전후의 입력 정책·정규화·해시·후처리 설정을 기록한다.
