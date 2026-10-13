"use client";
// Files: the deliverables with type and size, checkboxes, Download selected and Download all.
import { useParams } from "next/navigation";
import { FilesTab } from "@/components/ws/FilesTab";

export default function Page() {
  const params = useParams<{ domain: string }>();
  return <FilesTab domain={decodeURIComponent(params.domain)} />;
}
