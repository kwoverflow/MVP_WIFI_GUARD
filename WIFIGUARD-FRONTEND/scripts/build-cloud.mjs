// Build the shared production UI for the MQTT/Kafka/MLOps deployment, not Pi5 controls.
import { spawnSync } from "node:child_process";
import { writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const result = spawnSync(process.execPath, [resolve(root, "node_modules/vite/bin/vite.js"), "build"], {
  cwd: root, stdio: "inherit", shell: false,
  env: { ...process.env, VITE_PI5_LOCAL: "0", VITE_PC_SIMULATION: "0", VITE_API_BASE_URL: "", VITE_WS_URL: "", VITE_USE_MOCK: "0" },
});
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);
await writeFile(resolve(root, ".output/platform-build.json"), JSON.stringify({
  kind: "mlops-cloud-ui", pc_simulation: false, local_edge_controls: false,
  api_base: "same-origin", mock: false, built_at: new Date().toISOString(),
}) + "\n");
console.log("MLOPS_CLOUD_SAME_ORIGIN_BUILD_READY");
