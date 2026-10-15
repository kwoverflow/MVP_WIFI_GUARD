import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TrainingDashboard, measurement } from "../TrainingDashboard";

const { fetcher }=vi.hoisted(()=>({fetcher:vi.fn()}));
vi.mock("@/api/client",()=>({apiFetch:fetcher}));
vi.mock("@/api/auth",()=>({useAuth:()=>({user:{role:"USER"}})}));
vi.mock("../MonitoringHeader",()=>({Header:({title}:{title:string})=><h2>{title}</h2>}));
vi.mock("sonner",()=>({toast:{success:vi.fn(),error:vi.fn()}}));
const empty={checkedAt:new Date().toISOString(),host:{state:"unconfigured",cpuPercent:null,cpuCount:null,
  memoryUsedBytes:null,memoryTotalBytes:null,diskUsedBytes:null,diskTotalBytes:null,gpus:[]},
  inference:{state:"unconfigured",running:null,modelVersion:null,counts:{},processingMs:{}},
  notifications:{emailConfigured:false,mobilePushEnabled:false}};
const training={checkedAt:new Date().toISOString(),overall:"passed",mlflowHealthy:false,
  evidenceOnly:true,trainingRunning:null,automaticRetrainingConfigured:false,stages:[],
  metrics:{generalizationValidated:false,registeredModel:null,registeredModelVersion:null}};
function mount(systemOnly=false){return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><TrainingDashboard systemOnly={systemOnly}/></QueryClientProvider>);}
beforeEach(()=>fetcher.mockImplementation((path:string)=>Promise.resolve(path==="/training/resources"?empty:training)));
afterEach(()=>{cleanup();vi.clearAllMocks();});

describe("production MLOps UI",()=>{
  it("missing values are never fabricated as zero",()=>{
    expect(measurement(null,"%" )).toBe("미측정");
    expect(measurement(Number.NaN)).toBe("미측정");
    expect(measurement(0,"%" )).toBe("0%");
  });
  it("distinguishes saved artifacts from live training",async()=>{
    mount();
    await screen.findByText("학습·파인튜닝 기록");
    expect(screen.getByText(/현재 학습 중이거나 모델이 운영에 배포됐다는 뜻은 아닙니다/)).toBeTruthy();
    expect(screen.getByText("별도 구성 필요")).toBeTruthy();
    expect(screen.queryByText("학습 실행 중")).toBeNull();
  });
  it("system screen works without training history or Pi5 controls",async()=>{
    mount(true);
    await waitFor(()=>expect(fetcher).toHaveBeenCalledWith("/training/resources"));
    expect(screen.getByText("GPU 관측값이 없습니다")).toBeTruthy();
    expect(screen.queryByText("학습·파인튜닝 기록")).toBeNull();
    expect(screen.queryByText("수신 시작")).toBeNull();
    expect(fetcher).not.toHaveBeenCalledWith("/training/status");
  });
});
