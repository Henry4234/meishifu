/**
 * meishifu.org edge router
 *
 * /management/*      -> admin Cloud Run (/admin/*)
 * /api/*             -> backend Cloud Run
 * /assets/uploads/*  -> backend Cloud Run / Cloud Storage
 * everything else   -> frontend Cloud Run (/frontend/*)
 */

const MANAGEMENT_PREFIX = "/management";

function originUrl(origin: string, pathname: string, search: string): URL {
  const target = new URL(origin);
  target.pathname = pathname;
  target.search = search;
  return target;
}

function frontendPath(pathname: string): string {
  if (pathname === "/") return "/frontend/index.html";
  if (pathname.startsWith("/assets/")) return pathname;
  if (pathname.startsWith("/frontend/")) return pathname;
  return `/frontend${pathname}`;
}

function managementPath(pathname: string): string {
  const remainder = pathname.slice(MANAGEMENT_PREFIX.length);
  if (!remainder || remainder === "/") return "/admin/login.html";
  return `/admin${remainder}`;
}

function withSecurityHeaders(response: Response, isManagement: boolean): Response {
  const headers = new Headers(response.headers);
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  if (isManagement) headers.set("X-Robots-Tag", "noindex, nofollow");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function errorResponse(message: string, status: number): Response {
  return Response.json(
    {error: message},
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    },
  );
}

async function routeRequest(request: Request, env: Env): Promise<Response> {
  const incoming = new URL(request.url);
  const pathname = incoming.pathname;

  // 保留尾斜線，讓 login.html 的相對 js/css 解析到 /management/*。
  if (pathname === MANAGEMENT_PREFIX) {
    return Response.redirect(`${incoming.origin}${MANAGEMENT_PREFIX}/${incoming.search}`, 308);
  }

  let origin: string;
  let path: string;
  let isManagement = false;

  if (pathname.startsWith(`${MANAGEMENT_PREFIX}/`)) {
    origin = env.ADMIN_ORIGIN;
    path = managementPath(pathname);
    isManagement = true;
  } else if (
    pathname === "/api" ||
    pathname.startsWith("/api/") ||
    pathname.startsWith("/assets/uploads/")
  ) {
    origin = env.BACKEND_ORIGIN;
    path = pathname;
  } else {
    origin = env.FRONTEND_ORIGIN;
    path = frontendPath(pathname);
  }

  if (!origin) return errorResponse("Edge origin is not configured", 503);

  const target = originUrl(origin, path, incoming.search);
  const upstreamRequest = new Request(target, request);
  upstreamRequest.headers.set("X-Forwarded-Host", incoming.host);
  upstreamRequest.headers.set("X-Forwarded-Proto", "https");

  const response = await fetch(upstreamRequest);
  return withSecurityHeaders(response, isManagement);
}

export default {
  async fetch(request, env): Promise<Response> {
    try {
      return await routeRequest(request, env);
    } catch (error) {
      const incoming = new URL(request.url);
      console.error(JSON.stringify({
        event: "edge_upstream_error",
        method: request.method,
        path: incoming.pathname,
        message: error instanceof Error ? error.message : String(error),
      }));
      return errorResponse("Upstream service is unavailable", 502);
    }
  },
} satisfies ExportedHandler<Env>;
