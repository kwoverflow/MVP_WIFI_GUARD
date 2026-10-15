import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bell, CheckCircle2, Cpu, Database, RefreshCw, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { apiFetch } from "@/api/client";
import { useAuth } from "@/api/auth";
import { Header } from "./MonitoringHeader";

export interface TrainingStatus {
  checkedAt: string;
  overall: "passed" | "degraded";
  mlflowHealthy: boolean;
  evidenceOnly: boolean;
  trainingRunning: boolean | null;
  automaticRetrainingConfigured: boolean;
  stages: { id: string; label: string; status: "passed" | "missing"; detail: string }[];
  metrics: {
    featureRecordings: number | null;
    featureWindows: number | null;
    generalizationValidated: boolean | null;
    trainingPeakGpuBytes: number | null;
    finetunePeakGpuBytes: number | null;
    registeredModel: string | null;
    registeredModelVersion: string | null;
    mlflowRunId: string | null;
  };
}

export interface OperationResources {
  checkedAt: string;
  host: {
    state: string;
    cpuPercent: number | null;
    cpuCount: number | null;
    memoryUsedBytes: number | null;
    memoryTotalBytes: number | null;
    diskUsedBytes: number | null;
    diskTotalBytes: number | null;
    gpus: {
      index: number | null;
      name: string;
      utilizationPercent: number | null;
      memoryUsedMiB: number | null;
      memoryTotalMiB: number | null;
    }[];
  };
  inference: {
    state: string;
    running: boolean | null;
    modelVersion: string | null;
    counts: Record<string, number | null>;
    processingMs: Record<
      string,
      { count: number | null; median: number | null; p95: number | null; max: number | null }
    >;
  };
  notifications: { emailConfigured: boolean; mobilePushEnabled: boolean };
}

export const measurement = (value: number | null | undefined, suffix = "") =>
  value != null && Number.isFinite(value)
    ? `${value.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}${suffix}`
    : "미측정";
const gib = (value: number | null | undefined) =>
  measurement(value == null ? null : value / 2 ** 30, " GiB");
const stateText = (state?: string) =>
  state === "current" ? "수집 중" : state === "stale" ? "갱신 끊김" : "수집 대기";

export function TrainingDashboard({ systemOnly = false }: { systemOnly?: boolean }) {
  const cache = useQueryClient();
  const { user } = useAuth();
  const status = useQuery({
    queryKey: ["training-status"],
    queryFn: () => apiFetch<TrainingStatus>("/training/status"),
    enabled: !systemOnly,
    refetchInterval: 30000,
    retry: false,
  });
  const live = useQuery({
    queryKey: ["operation-resources"],
    queryFn: () => apiFetch<OperationResources>("/training/resources"),
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
    retry: false,
  });
  const validate = useMutation({
    mutationFn: () => apiFetch<TrainingStatus>("/training/validate", { method: "POST" }),
    onSuccess: (data) => {
      cache.setQueryData(["training-status"], data);
      toast.success("학습 산출물과 연결 상태를 확인했어요.");
    },
    onError: () => toast.error("상태를 확인하지 못했어요. 잠시 후 다시 시도해 주세요."),
  });
  const data = status.data;
  const resource = live.isError ? undefined : live.data;
  const host = resource?.host;
  const inference = resource?.inference;
  return (
    <div>
      <Header title={systemOnly ? "시스템 관리" : "모델 학습"} />
      <main className="operations-page">
        <section className="operations-intro">
          <div>
            <span className="operations-eyebrow">
              <ShieldCheck size={16} /> WIFI-GUARD 운영
            </span>
            <h1>
              {systemOnly ? "시스템을 안정적으로 살펴보세요" : "데이터에서 모델까지, 한곳에서"}
            </h1>
            <p>관제 서버의 자원 사용량과 피처 추출·추론 처리시간을 확인합니다.</p>
          </div>
          {!systemOnly && user?.role !== "MEMBER" && (
            <button
              className="operations-refresh"
              type="button"
              disabled={validate.isPending}
              onClick={() => validate.mutate()}
            >
              <RefreshCw size={16} className={validate.isPending ? "animate-spin" : ""} /> 상태 다시
              확인
            </button>
          )}
        </section>
        <section className="operations-topology" aria-label="파이프라인 구성">
          {["센서·재실 감지", "MQTT · Kafka", "피처 추출·추론", "DB 저장·알림"].map((text, i) => (
            <span key={text}>
              <b>{i + 1}</b>
              {text}
            </span>
          ))}
        </section>
        <section className="operations-section">
          <div className="operations-section-title">
            <h2>
              <Cpu size={18} /> 실시간 자원
            </h2>
            <span>{stateText(host?.state)}</span>
          </div>
          {live.isError && (
            <p role="alert" className="operations-notice">
              자원 상태를 불러오지 못했습니다. 수치를 정상으로 표시하지 않습니다.
            </p>
          )}
          <div className="operations-grid">
            <ResourceCard
              label="CPU"
              value={measurement(host?.cpuPercent, "%")}
              detail={`${measurement(host?.cpuCount)} 논리 코어 · 호스트 전체 기준`}
            />
            <ResourceCard
              label="메모리"
              value={gib(host?.memoryUsedBytes)}
              detail={`전체 ${gib(host?.memoryTotalBytes)}`}
            />
            <ResourceCard
              label="저장 공간"
              value={gib(host?.diskUsedBytes)}
              detail={`파일시스템 전체 ${gib(host?.diskTotalBytes)}`}
            />
            <ResourceCard
              label="GPU"
              value={measurement(host?.gpus[0]?.utilizationPercent, "%")}
              detail={
                host?.gpus[0]
                  ? `${host.gpus[0].name} · VRAM ${measurement(host.gpus[0].memoryUsedMiB, " MiB")}`
                  : "GPU 관측값이 없습니다"
              }
            />
          </div>
          <p className="operations-footnote">
            호스트 전체 사용량에는 다른 프로그램도 포함됩니다. 서비스별 사용량은
            Grafana/cAdvisor에서 구분합니다. 미수집·오래된 값은 0으로 표시하지 않습니다.
          </p>
        </section>
        <section className="operations-section">
          <div className="operations-section-title">
            <h2>
              <Activity size={18} /> 피처·추론 처리시간
            </h2>
            <span>{stateText(inference?.state)}</span>
          </div>
          <div className="operations-grid operations-grid-three">
            {(
              [
                ["features", "피처 추출"],
                ["inference", "모델 추론"],
                ["total", "합계"],
              ] as const
            ).map(([key, label]) => (
              <ResourceCard
                key={key}
                label={`${label} P95`}
                value={measurement(inference?.processingMs[key]?.p95, " ms")}
                detail={`중앙값 ${measurement(inference?.processingMs[key]?.median, " ms")}`}
              />
            ))}
          </div>
          <p className="operations-footnote">
            최근 최대 128개 처리 그룹의 윈도우당 배치 평균입니다. RAW 수신·네트워크·판정 대기를
            포함한 실제 알림 지연과는 다릅니다.
          </p>
        </section>
        <section className="operations-section operations-service-summary">
          <div>
            <h2>
              <Bell size={18} /> 알림 연결
            </h2>
            <p>
              이메일{" "}
              {resource
                ? resource.notifications.emailConfigured
                  ? "설정됨"
                  : "미설정"
                : "확인 대기"}{" "}
              · Android 푸시{" "}
              {resource
                ? resource.notifications.mobilePushEnabled
                  ? "활성화"
                  : "비활성화"
                : "확인 대기"}
            </p>
          </div>
          <small>서버 설정·발송 접수·휴대폰 수신·사람의 확인은 서로 다른 상태입니다.</small>
        </section>
        {!systemOnly && (
          <>
            {status.isLoading && (
              <p className="operations-notice">학습 산출물을 확인하고 있습니다.</p>
            )}
            {status.isError && (
              <p role="alert" className="operations-notice">
                학습 상태 API에 연결할 수 없습니다.
              </p>
            )}
            {data && (
              <section className="operations-section">
                <div className="operations-section-title">
                  <h2>
                    <Database size={18} /> 학습·파인튜닝 기록
                  </h2>
                  <span>
                    {data.mlflowHealthy ? "레지스트리 연결됨" : "레지스트리 연결 확인 필요"}
                  </span>
                </div>
                <p className="operations-footnote">
                  저장된 실행 산출물입니다. 현재 학습 중이거나 모델이 운영에 배포됐다는 뜻은
                  아닙니다. 상태 확인은 학습·모델 교체를 실행하지 않습니다.
                </p>
                <div className="operations-grid">
                  {data.stages.map((stage) => (
                    <article className="operations-card" key={stage.id}>
                      <span className="operations-label">{stage.label}</span>
                      <strong className="operations-stage-label">
                        {stage.status === "passed" ? (
                          <>
                            <CheckCircle2 size={17} /> 산출물 확인
                          </>
                        ) : (
                          "산출물 없음"
                        )}
                      </strong>
                      <small>{stage.detail}</small>
                    </article>
                  ))}
                </div>
                <dl className="operations-model-detail">
                  <div>
                    <dt>등록 모델</dt>
                    <dd>{data.metrics.registeredModel ?? "미등록"}</dd>
                  </div>
                  <div>
                    <dt>버전</dt>
                    <dd>{data.metrics.registeredModelVersion ?? "미등록"}</dd>
                  </div>
                  <div>
                    <dt>학습 실행</dt>
                    <dd>별도 학습 작업에서 실행</dd>
                  </div>
                  <div>
                    <dt>자동 재학습</dt>
                    <dd>{data.automaticRetrainingConfigured ? "설정됨" : "별도 구성 필요"}</dd>
                  </div>
                </dl>
                {data.metrics.generalizationValidated !== true && (
                  <p className="operations-notice">
                    실행 산출물 확인과 낙상 정확도 검증은 다릅니다. 독립된 검증 데이터와 실제
                    환경에서의 성능 확인이 필요합니다.
                  </p>
                )}
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function ResourceCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="operations-card">
      <span className="operations-label">{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}
