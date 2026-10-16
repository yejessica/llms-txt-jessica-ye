import { type NextRequest, NextResponse } from "next/server";

export function GET(request: NextRequest) {
  const path = request.nextUrl.pathname.slice(1);
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
  const destination = new URL("/", request.url);
  // Keep the raw escaped path and query; decoding path segments would turn
  // encoded slashes, question marks, or percent signs into different URLs.
  destination.searchParams.set("url", target + request.nextUrl.search);
  return NextResponse.redirect(destination, { status: 307, headers: { "Cache-Control": "no-store" } });
}
