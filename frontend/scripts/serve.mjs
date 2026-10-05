import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer, request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const host = process.env.HOST ?? "0.0.0.0";
const port = Number(process.env.PORT ?? "5173");
const proxyTarget = new URL(
  process.env.FINANCE_FRONTEND_PROXY_TARGET ?? "http://127.0.0.1:8000",
);
const root = resolve(fileURLToPath(new URL("../dist", import.meta.url)));
const mimeTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".ico", "image/x-icon"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".map", "application/json; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".webp", "image/webp"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
]);

function proxy(request, response) {
  const upstreamUrl = new URL(request.url ?? "/", proxyTarget);
  const transport =
    upstreamUrl.protocol === "https:" ? httpsRequest : httpRequest;
  const headers = { ...request.headers, host: upstreamUrl.host };
  delete headers.connection;

  const upstream = transport(
    upstreamUrl,
    { method: request.method, headers },
    (upstreamResponse) => {
      response.writeHead(
        upstreamResponse.statusCode ?? 502,
        upstreamResponse.headers,
      );
      upstreamResponse.pipe(response);
    },
  );
  upstream.on("error", () => {
    if (response.headersSent) {
      response.destroy();
      return;
    }
    response.writeHead(502, {
      "content-type": "application/json; charset=utf-8",
    });
    response.end(
      JSON.stringify({
        error: {
          code: "frontend_proxy_unavailable",
          message: "Backend API is unavailable",
        },
      }),
    );
  });
  request.pipe(upstream);
}

async function staticResponse(request, response) {
  if (!["GET", "HEAD"].includes(request.method ?? "GET")) {
    response.writeHead(405, { allow: "GET, HEAD" });
    response.end();
    return;
  }
  const requestUrl = new URL(request.url ?? "/", "http://frontend.local");
  const requestedPath = decodeURIComponent(requestUrl.pathname);
  const relativePath =
    requestedPath === "/" ? "index.html" : requestedPath.slice(1);
  const candidate = resolve(root, relativePath);
  const insideRoot =
    candidate === root || candidate.startsWith(`${root}${sep}`);
  let filePath = insideRoot ? candidate : resolve(root, "index.html");

  try {
    const file = await stat(filePath);
    if (!file.isFile()) filePath = resolve(root, "index.html");
  } catch {
    if (extname(relativePath)) {
      response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      response.end("Not found");
      return;
    }
    filePath = resolve(root, "index.html");
  }

  const extension = extname(filePath);
  const immutable = filePath.includes(`${sep}assets${sep}`);
  response.writeHead(200, {
    "cache-control": immutable
      ? "public, max-age=31536000, immutable"
      : "no-cache",
    "content-type": mimeTypes.get(extension) ?? "application/octet-stream",
    "x-content-type-options": "nosniff",
  });
  if (request.method === "HEAD") {
    response.end();
    return;
  }
  createReadStream(filePath).pipe(response);
}

const server = createServer((request, response) => {
  const pathname = new URL(request.url ?? "/", "http://frontend.local")
    .pathname;
  if (
    pathname === "/api" ||
    pathname.startsWith("/api/") ||
    pathname.startsWith("/health")
  ) {
    proxy(request, response);
    return;
  }
  void staticResponse(request, response).catch(() => {
    if (!response.headersSent) {
      response.writeHead(500, {
        "content-type": "application/json; charset=utf-8",
      });
      response.end(
        JSON.stringify({
          error: {
            code: "frontend_static_error",
            message: "Frontend asset could not be served",
          },
        }),
      );
      return;
    }
    response.destroy();
  });
});

server.listen(port, host, () => {
  process.stdout.write(`Frontend listening on http://${host}:${port}\n`);
});

function shutdown() {
  server.close(() => process.exit(0));
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
