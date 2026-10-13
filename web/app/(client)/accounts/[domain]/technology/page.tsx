"use client";
// The technology tab: a plain data table, sorted and filtered on the server.
import { Suspense } from "react";
import { useParams } from "next/navigation";
import { TableScreen } from "@/components/ws/TableScreen";

export default function Page() {
  const params = useParams<{ domain: string }>();
  return <Suspense><TableScreen domain={decodeURIComponent(params.domain)} table="tech" /></Suspense>;
}
