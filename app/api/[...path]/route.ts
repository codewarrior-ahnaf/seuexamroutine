import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = (process.env.API_BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const BACKEND_ORIGIN = process.env.FRONTEND_ORIGIN ?? "http://localhost:3000";
const ALLOWED_ROUTES = new Set([
  "api/status",
  "api/exams",
  "api/exams/schedule",
  "api/auth/google",
  "api/auth/callback",
]);

async function proxyRequest(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const route = `api/${path.join("/")}`;
  if (!ALLOWED_ROUTES.has(route)) {
    return NextResponse.json({ detail: "API route not found." }, { status: 404 });
  }

  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  const cookie = request.headers.get("cookie");
  const origin = request.headers.get("origin");
  if (
    (request.method === "POST" || request.method === "DELETE") &&
    origin &&
    origin !== request.nextUrl.origin
  ) {
    return NextResponse.json({ detail: "Request origin is not allowed." }, { status: 403 });
  }
  if (contentType) headers.set("content-type", contentType);
  if (cookie) headers.set("cookie", cookie);
  if (request.method === "POST" || request.method === "DELETE") {
    headers.set("origin", BACKEND_ORIGIN);
  }

  try {
    const response = await fetch(`${BACKEND_URL}/${route}${request.nextUrl.search}`, {
      method: request.method,
      headers,
      body: request.method === "GET" ? undefined : await request.arrayBuffer(),
      cache: "no-store",
      redirect: "manual",
    });
    const responseHeaders = new Headers();
    const responseType = response.headers.get("content-type");
    const location = response.headers.get("location");
    if (responseType) responseHeaders.set("content-type", responseType);
    if (location) responseHeaders.set("location", location);
    for (const cookieHeader of response.headers.getSetCookie()) {
      responseHeaders.append("set-cookie", cookieHeader);
    }
    responseHeaders.set("cache-control", "no-store");

    return new Response(response.body, {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error(`Could not proxy ${request.method} ${route} to the Python API.`, error);
    return NextResponse.json(
      {
        detail: process.env.NODE_ENV === "production"
          ? "The exam service is temporarily unavailable. Please try again shortly."
          : "Could not reach the Python API. Start both services with: npm run dev:all",
      },
      { status: 502 },
    );
  }
}

export const GET = proxyRequest;
export const POST = proxyRequest;
export const DELETE = proxyRequest;
