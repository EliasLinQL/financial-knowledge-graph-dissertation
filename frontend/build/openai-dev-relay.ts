import { fetch as undiciFetch, ProxyAgent } from "undici";
import type { Plugin } from "vite";

const RELAY_PATH = "/__openai_responses";
const OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses";
const MAX_BODY_BYTES = 128 * 1024;
const UPSTREAM_TIMEOUT_MS = 45_000;

const proxyUrlFromEnvironment = () =>
  process.env.HTTPS_PROXY?.trim()
  || process.env.HTTP_PROXY?.trim()
  || process.env.ALL_PROXY?.trim()
  || "";

const isLoopback = (address = "") =>
  address === "127.0.0.1"
  || address === "::1"
  || address.startsWith("::ffff:127.");

function sendJson(response: import("node:http").ServerResponse, status: number, body: unknown) {
  response.statusCode = status;
  response.setHeader("content-type", "application/json; charset=utf-8");
  response.setHeader("cache-control", "no-store");
  response.setHeader("x-content-type-options", "nosniff");
  response.end(JSON.stringify(body));
}

async function readRequestBody(request: import("node:http").IncomingMessage) {
  const chunks: Buffer[] = [];
  let byteLength = 0;
  let tooLarge = false;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    byteLength += buffer.byteLength;
    if (byteLength > MAX_BODY_BYTES) {
      tooLarge = true;
      continue;
    }
    chunks.push(buffer);
  }
  if (tooLarge) throw new RangeError("request body too large");
  return Buffer.concat(chunks).toString("utf8");
}

/**
 * Development-only bridge from the local Worker to OpenAI through an explicit
 * HTTP(S) proxy. It never runs in a production build, never accepts a target
 * URL from the caller, and never logs or returns credentials/upstream bodies.
 */
export function openAIDevRelay(): Plugin {
  const proxyUrl = proxyUrlFromEnvironment();
  return {
    name: "openai-dev-relay",
    apply: "serve",
    enforce: "pre",
    configureServer(server) {
      if (!proxyUrl) return;
      const dispatcher = new ProxyAgent(proxyUrl);
      server.httpServer?.once("close", () => void dispatcher.close());

      server.middlewares.use(RELAY_PATH, async (request, response) => {
        if (!isLoopback(request.socket.remoteAddress)) {
          sendJson(response, 403, { error: "Local relay only" });
          return;
        }
        if (request.method !== "POST") {
          sendJson(response, 405, { error: "Method not allowed" });
          return;
        }
        if (!String(request.headers["content-type"] ?? "").toLowerCase().startsWith("application/json")) {
          sendJson(response, 415, { error: "JSON body required" });
          return;
        }
        const authorization = String(request.headers.authorization ?? "");
        if (!/^Bearer\s+\S+$/i.test(authorization)) {
          sendJson(response, 401, { error: "Authorization required" });
          return;
        }

        try {
          const body = await readRequestBody(request);
          const parsed = JSON.parse(body) as unknown;
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            sendJson(response, 400, { error: "Invalid JSON body" });
            return;
          }
          const upstream = await undiciFetch(OPENAI_RESPONSES_URL, {
            method: "POST",
            headers: {
              authorization,
              "content-type": "application/json",
            },
            body,
            dispatcher,
            signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
          });
          if (!upstream.ok) {
            await upstream.body?.cancel();
            sendJson(response, upstream.status, { error: "OpenAI request rejected" });
            return;
          }
          response.statusCode = upstream.status;
          response.setHeader("content-type", upstream.headers.get("content-type") || "application/json; charset=utf-8");
          response.setHeader("cache-control", "no-store");
          response.setHeader("x-content-type-options", "nosniff");
          response.end(Buffer.from(await upstream.arrayBuffer()));
        } catch (error) {
          sendJson(response, error instanceof RangeError ? 413 : 502, {
            error: error instanceof RangeError ? "Request body too large" : "OpenAI relay unavailable",
          });
        }
      });
    },
  };
}

export const OPENAI_DEV_RELAY_ENABLED = Boolean(proxyUrlFromEnvironment());
