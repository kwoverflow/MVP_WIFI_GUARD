import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Device } from "@/lib/domain";
import { RemoteDevicePanel } from "../RemoteDevicePanel";
const state = vi.hoisted(() => ({ role: "USER", commands: [] as unknown[] }));
vi.mock("@/api/auth", () => ({ useAuth: () => ({ user: { role: state.role } }) }));
vi.mock("@/api/queries", () => ({ useConfig: () => ({ data: {} }) }));
vi.mock("@/api/client", () => ({ apiFetch: () => Promise.resolve(state.commands) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
function mount() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RemoteDevicePanel device={{ id: "device", connection: "MQTT" } as Device} />
    </QueryClientProvider>,
  );
}
afterEach(() => cleanup());
it("a submitted command is still waiting, not completed", async () => {
  state.role = "USER";
  state.commands = [{ id: "cmd", action: "calibrate", state: "SENT", result: null }];
  mount();
  await screen.findByText(/장치 응답 대기/);
  expect(screen.queryByText(/장치 완료 확인/)).toBeNull();
  expect(
    (screen.getByRole("button", { name: "빈방 보정 요청" }) as HTMLButtonElement).disabled,
  ).toBe(true);
});
it("members see device state without control buttons", async () => {
  state.role = "MEMBER";
  state.commands = [];
  mount();
  await screen.findByText("원격 명령 이력이 없습니다.");
  expect(screen.queryByRole("button", { name: "감지 설정 적용" })).toBeNull();
});
