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