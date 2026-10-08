// Structured client error reporting: an error code, the route name and the request id, never
// content (section 9). The API has no client-error endpoint yet (docs/frontend/screen-map.md), so the
// sink is local: development logs to the console, production keeps the last few in memory for the
// error boundary to show. A sink can be installed when an endpoint exists.
export interface ClientError { code: string; route: string; requestId: string | null }
type Sink = (e: ClientError) => void;

const recent: ClientError[] = [];
let sink: Sink = (e) => {
  recent.push(e);
  if (recent.length > 20) recent.shift();
  if (process.env.NODE_ENV === "development") console.warn("client error", e.code, e.route, e.requestId ?? "-");
};

export function report(code: string, route: string, requestId: string | null = null): void {
  const safe = { code: code.replace(/[^a-z0-9_.:-]/gi, "").slice(0, 60), route: route.replace(/[^a-z0-9_/{}.-]/gi, "").slice(0, 120), requestId: requestId && /^[a-z0-9-]{8,64}$/i.test(requestId) ? requestId : null };
  sink(safe);
}

export function installSink(next: Sink): void { sink = next; }
export function recentErrors(): readonly ClientError[] { return recent; }
