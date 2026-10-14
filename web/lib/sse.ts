// Server-sent event frames from a fetch response body (the Ask stream). A frame that is not
// valid JSON is reported as an "invalid" frame so the caller can show an error instead of
// silently dropping part of an answer.
import { ApiError } from "@/lib/api";

export interface Frame { event: string; data: Record<string, unknown> }

export function parseFrame(raw: string): Frame | null {
  let event = "message";
  const data: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  try {
    const value: unknown = JSON.parse(data.join("\n"));
    return typeof value === "object" && value !== null ? { event, data: value as Record<string, unknown> } : { event: "invalid", data: {} };
  } catch {
    return { event: "invalid", data: {} };
  }
}

export async function readFrames(reply: Response, onFrame: (frame: Frame) => void): Promise<void> {
  const reader = reply.body?.getReader();
  if (!reader) throw new ApiError(502, "stream_unavailable", "");
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = parseFrame(buffer.slice(0, boundary));
      if (frame) onFrame(frame);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
}
