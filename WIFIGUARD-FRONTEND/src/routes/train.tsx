import { createFileRoute } from "@tanstack/react-router";
import { TrainingDashboard } from "@/components/TrainingDashboard";

export const Route = createFileRoute("/train")({
  head: () => ({ meta: [{ title: "모델 학습 · WIFI-GUARD" }] }),
  component: TrainingDashboard,
});
