"use client";
import { RouteError } from "@/ui/RouteError";

export default function Error(props: { error: Error & { digest?: string }; reset: () => void; retry?: () => void }) {
  return <RouteError {...props} />;
}
