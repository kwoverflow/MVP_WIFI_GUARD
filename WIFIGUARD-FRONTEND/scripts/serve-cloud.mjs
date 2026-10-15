import http from "node:http";
import { Readable } from "node:stream";
import worker from "../dist/server/server.js";

const port = Number(process.env.UI_PORT || 4173);
const host = process.env.UI_HOST || "127.0.0.1";
const server = http.createServer(async (incoming, outgoing) => {
  try {
    if (!["GET", "HEAD"].includes(incoming.method)) return outgoing.writeHead(405).end();
    const request = new Request(`http://${incoming.headers.host || `127.0.0.1:${port}`}${incoming.url}`, {
      method: incoming.method, headers: incoming.headers,
    });
    const response = await worker.fetch(request);
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    if (incoming.method === "HEAD" || !response.body) outgoing.end();
    else Readable.fromWeb(response.body).pipe(outgoing);
  } catch {
    if (!outgoing.headersSent) outgoing.writeHead(500);
    outgoing.end("UI unavailable");
  }
});
server.requestTimeout = 15000;
server.listen(port, host, () => console.log(`WIFI_GUARD_UI_READY port=${port}`));
for (const signal of ["SIGTERM", "SIGINT"]) process.on(signal, () => server.close(() => process.exit(0)));
