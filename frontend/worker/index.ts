/** Cloudflare Worker entry point for the analyst dashboard. */
import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";
import { handleResearchAssistant } from "./research-assistant";

interface Env {
  ASSETS?: Fetcher;
  OPENAI_API_KEY?: string;
  OPENAI_MODEL?: string;
  OPENAI_LOCAL_RELAY_ENABLED?: string;
  IMAGES: {
    input(stream: ReadableStream): {
      transform(options: Record<string, unknown>): {
        output(options: { format: string; quality: number }): Promise<{ response(): Response }>;
      };
    };
  };
}

interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
  passThroughOnException(): void;
}

// Image security config. SVG sources with .svg extension auto-skip the
// optimization endpoint on the client side (served directly, no proxy).
// To route SVGs through the optimizer (with security headers), set
// dangerouslyAllowSVG: true in next.config.js and uncomment below:
// const imageConfig: ImageConfig = { dangerouslyAllowSVG: true };

const worker = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/_vinext/image") {
      const allowedWidths = [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES];
      return handleImageOptimization(request, {
        fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
        transformImage: async (body, { width, format, quality }) => {
          const result = await env.IMAGES.input(body).transform(width > 0 ? { width } : {}).output({ format, quality });
          return result.response();
        },
      }, allowedWidths);
    }

    if (url.pathname === "/api/research-assistant") {
      try {
        const snapshotRequest = new Request(new URL("/data/dashboard.json", request.url));
        // A deployed Worker receives the ASSETS binding. Vinext's local dev
        // server serves /public files directly and does not inject that binding,
        // so use a same-origin fetch there.
        const snapshotResponse = env?.ASSETS
          ? await env.ASSETS.fetch(snapshotRequest)
          : await fetch(snapshotRequest);
        if (!snapshotResponse.ok) throw new Error(`Snapshot returned ${snapshotResponse.status}`);
        const snapshot = await snapshotResponse.json();
        return handleResearchAssistant(request, snapshot, env ?? {});
      } catch {
        return new Response(JSON.stringify({ error: "Checked dashboard data is unavailable" }), {
          status: 503,
          headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
        });
      }
    }

    return handler.fetch(request, env, ctx);
  },
};

export default worker;
