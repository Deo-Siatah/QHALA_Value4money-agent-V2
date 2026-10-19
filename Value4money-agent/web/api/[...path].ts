// The token's home on the deployed frontend.
//
// The Render backend requires X-API-Token on every request, and the browser must never hold that
// token: EventSource cannot set headers, and a token shipped in the page would sit in the public
// bundle. In development the Vite proxy adds the header on the way past (web/vite.config.ts); this
// is the same idea for production. The browser calls the relative /api paths it already uses, and
// this function forwards them to the backend server-to-server with the header attached.
//
// BACKEND_URL and API_TOKEN are set on the Vercel project as server-side variables with no VITE_
// prefix, so Vite never inlines them into the client and they exist only inside this function.
//
// The response body is passed through as a stream rather than buffered: one of these routes is the
// run's server-sent event stream, and a buffered stream would only ever arrive all at once.

export const config = { maxDuration: 300 };

const BACKEND_URL = (process.env.BACKEND_URL ?? "").replace(/\/$/, "");
const API_TOKEN = process.env.API_TOKEN ?? "";

export default async function handler(request: Request): Promise<Response> {
  if (!BACKEND_URL || !API_TOKEN) {
    return json(500, {
      detail: "BACKEND_URL and API_TOKEN must be set on this Vercel project",
    });
  }

  const url = new URL(request.url);
  const headers: Record<string, string> = { "x-api-token": API_TOKEN };
  for (const name of ["content-type", "accept"]) {
    const value = request.headers.get(name);
    if (value) {
      headers[name] = value;
    }
  }

  // A GET or HEAD has no body; passing one to fetch would make Node reject the request.
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const upstream = await fetch(`${BACKEND_URL}${url.pathname}${url.search}`, {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    // Node's fetch needs this whenever the body is a stream instead of a buffer.
    duplex: hasBody ? "half" : undefined,
  } as RequestInit);

  const responseHeaders = new Headers({ "cache-control": "no-store" });
  for (const name of ["content-type", "content-disposition"]) {
    const value = upstream.headers.get(name);
    if (value) {
      responseHeaders.set(name, value);
    }
  }

  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

function json(status: number, body: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
