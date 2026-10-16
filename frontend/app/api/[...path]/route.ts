import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

// A server-side proxy keeps the backend origin private and works with runtime
// environment variables on a VPS. Only the frontend's actual API routes pass.
async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join("/");
  const allowed = request.method === "GET"
    ? /^(health|jobs\/[\w-]+|websites\/[\w-]+(\/(llms\.txt|download))?)$/.test(route)
    : /^(generate|websites\/[\w-]+\/refresh)$/.test(route);
  if (!allowed) return Response.json({ error: { code: "not_found" } }, { status: 404 });

  const origin = process.env.BACKEND_URL?.replace(/\/$/, "");
  if (!origin) {
    return Response.json({ error: { code: "backend_unconfigured" } }, { status: 503 });
  }
  try {
    const response = await fetch(`${origin}/api/${route}`, {
      method: request.method,
      headers: request.method === "POST" ? { "Content-Type": "application/json" } : {},
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(20_000)]),
    });
    const headers = new Headers({ "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
    for (const name of ["Content-Type", "Content-Disposition", "Retry-After"]) {
      const value = response.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(await response.text(), { status: response.status, headers });
  } catch (error) {
    const timeout = error instanceof Error && error.name === "TimeoutError";
    return Response.json({ error: { code: timeout ? "timeout" : "backend_unavailable" } }, { status: timeout ? 504 : 502 });
  }
}

export { proxy as GET, proxy as POST };
