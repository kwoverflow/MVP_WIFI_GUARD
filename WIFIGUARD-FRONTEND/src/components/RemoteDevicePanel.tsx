import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/api/auth";
import { apiFetch } from "@/api/client";
import { useConfig } from "@/api/queries";
import type { Device } from "@/lib/domain";
import { toast } from "sonner";

export interface RemoteCommand {
  id: string;
  action: string;
  state: string;
  created_at: string;
  expires_at: string;
  stage: string | null;
  progress: number | null;
  error: string | null;
  result: { effective?: Record<string, number> } | null;
}
const pending = new Set(["PENDING", "SENT", "ACCEPTED", "PROGRESS"]);
const labels: Record<string, string> = {
  PENDING: "전송 대기",
  SENT: "장치 응답 대기",
  ACCEPTED: "장치 접수",
  PROGRESS: "장치 처리 중",
  DONE: "장치 완료 확인",
  ERROR: "장치 처리 실패",
  EXPIRED: "응답 기한 만료",
};
const metric = (value: number) => (Number.isFinite(value) ? value.toFixed(3) : "미확인");

export function RemoteDevicePanel({ device }: { device: Device }) {
  const { user } = useAuth();
  const client = useQueryClient();
  const { data: config } = useConfig();
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const query = useQuery({
    queryKey: ["remote-commands", device.id],
    queryFn: () => apiFetch<RemoteCommand[]>(`/devices/${device.id}/commands`),
    refetchInterval: 2000,
  });
  const latest = query.data?.[0];
  const canEdit = user?.role === "USER" || user?.role === "ROOT";
  const waiting = busy || Boolean(latest && pending.has(latest.state));
  useEffect(() => {
    if (query.dataUpdatedAt) void client.invalidateQueries({ queryKey: ["devices"] });
  }, [query.dataUpdatedAt, client]);
  async function send(action: "calibrate" | "set_config" | "ping", args: Record<string, unknown>) {
    setBusy(true);
    try {
      await apiFetch(`/devices/${device.id}/commands`, { method: "POST", body: { action, args } });
      await query.refetch();
      toast.success("요청을 저장했습니다. 장치 완료 응답을 기다립니다.");
      setEditing(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "장치 제어 요청 실패");
    } finally {
      setBusy(false);
    }
  }
  function edit() {
    setValues({
      presence_mv_threshold: String(device.presenceMvThreshold ?? config?.presenceMvThreshold ?? 2),
      wander_baseline: String(device.wanderBaseline ?? 0.5),
      wander_ratio_threshold: String(config?.wanderRatioThreshold ?? 1.8),
      presence_timeout_s: String(config?.presenceTimeoutS ?? 10),
    });
    setEditing(true);
  }
  return (
    <section className="space-y-3 rounded border border-border p-3" aria-label="원격 장치 제어">
      <h3 className="font-semibold">원격 장치 제어</h3>
      <p className="text-xs text-muted">
        빈방 보정은 퇴실 대기 30초와 CSI 측정 30초 후 완료됩니다. 보정 중에는 낙상 추론 신호 전송이
        일시 중지됩니다.
      </p>
      {query.isError ? (
        <p role="alert">명령 상태를 불러오지 못했습니다.</p>
      ) : latest ? (
        <div aria-live="polite" className="text-sm space-y-1">
          <p>
            {latest.action === "calibrate"
              ? "빈방 보정"
              : latest.action === "set_config"
                ? "감지 설정"
                : "연결 확인"}{" "}
            · {labels[latest.state] ?? latest.state}
          </p>
          {latest.stage && (
            <p>
              {latest.stage === "LEAVING"
                ? "공간을 비워 주세요"
                : latest.stage === "MEASURING"
                  ? "빈방 신호 측정 중"
                  : latest.stage}{" "}
              · {Math.round((latest.progress ?? 0) * 100)}%
            </p>
          )}
          {latest.error && <p role="alert">{latest.error}</p>}
          {latest.state === "DONE" && latest.result?.effective && (
            <p>
              장치 적용 기준값: MV {metric(latest.result.effective.presence_mv_threshold)} · 재실{" "}
              {metric(latest.result.effective.wander_baseline)}
            </p>
          )}
        </div>
      ) : (
        <p className="text-xs text-muted">원격 명령 이력이 없습니다.</p>
      )}
      {canEdit && (
        <div className="flex flex-wrap gap-2">
          <button
            disabled={waiting || query.isError}
            className="rounded bg-primary text-primary-foreground px-3 py-2 disabled:opacity-50"
            onClick={() => {
              if (
                window.confirm(
                  "공간을 비운 뒤 진행하세요. 보정 중에는 낙상 추론이 잠시 중지됩니다. 빈방 보정을 요청할까요?",
                )
              )
                void send("calibrate", { empty_room_confirmed: true });
            }}
          >
            빈방 보정 요청
          </button>
          <button
            disabled={waiting || query.isError}
            className="rounded border border-border px-3 py-2 disabled:opacity-50"
            onClick={edit}
          >
            감지 설정 적용
          </button>
          <button
            disabled={waiting || query.isError}
            className="rounded border border-border px-3 py-2 disabled:opacity-50"
            onClick={() => void send("ping", {})}
          >
            연결 확인
          </button>
        </div>
      )}
      {editing && (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            const args = Object.fromEntries(
              Object.entries(values).map(([key, value]) => [key, Number(value)]),
            );
            if (Object.values(args).some((v) => !Number.isFinite(v) || v <= 0)) {
              toast.error("양수 기준값을 입력하세요.");
              return;
            }
            if (window.confirm("표시한 감지 기준값을 장치에 적용할까요?"))
              void send("set_config", args);
          }}
        >
          {Object.entries(values).map(([key, value]) => (
            <label className="block text-xs" key={key}>
              {
                {
                  presence_mv_threshold: "움직임 임계값",
                  wander_baseline: "재실 기준값",
                  wander_ratio_threshold: "미세 움직임 비율",
                  presence_timeout_s: "재실 유지 시간(초)",
                }[key]
              }
              <input
                type="number"
                step="any"
                min="0.000001"
                required
                className="block rounded border border-border bg-background p-2 w-full"
                value={value}
                onChange={(e) => setValues({ ...values, [key]: e.target.value })}
              />
            </label>
          ))}
          <button
            disabled={waiting}
            type="submit"
            className="rounded bg-primary text-primary-foreground px-3 py-2"
          >
            장치에 적용 요청
          </button>
          <button type="button" onClick={() => setEditing(false)} className="px-3 py-2">
            닫기
          </button>
        </form>
      )}
    </section>
  );
}
