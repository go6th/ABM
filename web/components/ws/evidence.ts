// The evidence a citation points at. Shaped for the backend's fact and source records; see
// docs/FRONTEND_BACKEND_GAPS.md (G-04, G-05) for the fields the answer stream does not carry yet.
import type { Grade } from "./GradeMark";

export interface Evidence {
  n: number;
  grade: Grade;
  reason: string | null;
  title: string;
  sourceName: string;
  retrieved: string | null;
  excerpt: string | null;
  /** [start, end) of the supporting phrase inside the excerpt, when the backend supplies it. */
  span: [number, number] | null;
  /** Safe http(s) link, or null when the link is dead (shown as "Archive record"). */
  url: string | null;
}
