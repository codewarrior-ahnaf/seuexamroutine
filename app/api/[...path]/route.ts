import { NextRequest, NextResponse } from "next/server";

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
  const isMutation = request.method === "POST" || request.method === "DELETE";
  const contentType = request.headers.get("content-type");
  const cookie = request.headers.get("cookie");
  const origin = request.headers.get("origin");
  if (isMutation && (!origin || origin !== request.nextUrl.origin)) {
    return NextResponse.json({ detail: "Request origin is not allowed." }, { status: 403 });
  }

  const configuredBackend = process.env.API_BACKEND_URL?.trim();
  const backendOrigin = process.env.FRONTEND_ORIGIN?.trim().replace(/\/+$/, "");
  if (process.env.NODE_ENV === "production" && (!configuredBackend || !backendOrigin)) {
    console.error("API_BACKEND_URL and FRONTEND_ORIGIN must be set for the production API proxy.");
    return NextResponse.json(
      { detail: "The exam service is not configured. Please contact the site administrator." },
      { status: 503 },
    );
  }

  let backendUrl: URL;
  try {
    backendUrl = new URL(configuredBackend || "http://127.0.0.1:8000");
  } catch {
    return NextResponse.json(
      { detail: "The exam service is not configured. Please contact the site administrator." },
      { status: 503 },
    );
  }
  if (
    backendUrl.username ||
    backendUrl.password ||
    backendUrl.search ||
    backendUrl.hash ||
    (backendUrl.pathname !== "/" && backendUrl.pathname !== "") ||
    (process.env.NODE_ENV === "production" && backendUrl.protocol !== "https:")
  ) {
    console.error("API_BACKEND_URL must be a public HTTPS origin without credentials, path, query, or fragment.");
    return NextResponse.json(
      { detail: "The exam service is not configured. Please contact the site administrator." },
      { status: 503 },
    );
  }

  if (contentType) headers.set("content-type", contentType);
  if (cookie) headers.set("cookie", cookie);
  if (isMutation) {
    headers.set("origin", backendOrigin || "http://localhost:3000");
  }

  try {
    const response = await fetch(`${backendUrl.origin}/${route}${request.nextUrl.search}`, {
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
