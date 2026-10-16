import type { NextRequest } from "next/server";

export function GET(request: NextRequest) {
  const path = request.nextUrl.pathname.slice(1);
  // A guessed metadata filename is not a hostname shortcut. Specific metadata
  // routes, if added later, take precedence over this catch-all.
  const firstSegment = path.split("/", 1)[0];
  if (/\.(?:xml(?:\.gz)?|txt|json|webmanifest|ico)$/i.test(firstSegment)) {
    return new Response("Not found", { status: 404 });
  }
  // Leave ordinary unknown app routes as 404s. API and static routes take
  // precedence over this catch-all and retain their existing behavior.
  if (!/^[^/]+\.[^/]+/.test(path) && !/^[a-z][a-z\d+.-]*:\//i.test(path)) {
    return new Response("Not found", { status: 404 });
  }
  // Next.js normalizes repeated slashes before routing, so a pasted
  // https://example.com may arrive here as https:/example.com.
  const target = /^https?:\//i.test(path)
    ? path.replace(/^(https?):\/+/i, "$1://")
    : /^[a-z][a-z\d+.-]*:\//i.test(path) ? path : `https://${path}`;
  // Keep the raw escaped path and query; decoding path segments would turn
  // encoded slashes, question marks, or percent signs into different URLs.
  const query = new URLSearchParams({ url: target + request.nextUrl.search });
  // A relative Location keeps the browser on its public origin. Behind Render's
  // reverse proxy, request.url can contain the internal 0.0.0.0:10000 address.
  return new Response(null, {
    status: 307,
    headers: { Location: `/?${query}`, "Cache-Control": "no-store" },
  });
}
