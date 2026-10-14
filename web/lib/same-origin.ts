interface SameOriginRequest {
  origin: string | null;
  host: string | null;
  forwardedHost: string | null;
  protocol: string;
  forwardedProtocol: string | null;
}

export function isSameOriginRequest(request: SameOriginRequest): boolean {
  if (!request.origin) return false;

  const host = request.host?.trim() || request.forwardedHost?.split(",", 1)[0]?.trim();
  const protocol = (request.forwardedProtocol?.split(",", 1)[0]?.trim() || request.protocol)
    .replace(/:$/, "")
    .toLowerCase();
  if (!host || (protocol !== "http" && protocol !== "https")) return false;

  try {
    const origin = new URL(request.origin);
    const expected = new URL(`${protocol}://${host}`);
    return !origin.username
      && !origin.password
      && origin.pathname === "/"
      && !origin.search
      && !origin.hash
      && origin.origin === expected.origin;
  } catch {
    return false;
  }
}

interface HeaderRequest {
  headers: { get(name: string): string | null };
  nextUrl: { protocol: string; host?: string };
}

// The same check for a route handler's request. Never compare with `request.url`: behind a port
// mapping or a load balancer it is the server's own address, not the one the browser used.
export function isSameOriginRouteRequest(request: HeaderRequest): boolean {
  return isSameOriginRequest({
    origin: request.headers.get("origin"),
    host: request.headers.get("host"),
    forwardedHost: request.headers.get("x-forwarded-host"),
    protocol: request.nextUrl.protocol,
    forwardedProtocol: request.headers.get("x-forwarded-proto"),
  });
}

// The origin the browser used (Host and X-Forwarded-* headers, else the URL's host), for comparing
// with a configured public URL; null without a usable host or protocol.
export function publicOrigin(request: HeaderRequest): string | null {
  const host = request.headers.get("host")?.trim()
    || request.headers.get("x-forwarded-host")?.split(",", 1)[0]?.trim()
    || request.nextUrl.host;
  const protocol = (request.headers.get("x-forwarded-proto")?.split(",", 1)[0]?.trim() || request.nextUrl.protocol)
    .replace(/:$/, "")
    .toLowerCase();
  if (!host || (protocol !== "http" && protocol !== "https")) return null;
  try {
    return new URL(`${protocol}://${host}`).origin;
  } catch {
    return null;
  }
}
