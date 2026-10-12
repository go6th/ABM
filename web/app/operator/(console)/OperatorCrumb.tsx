"use client";
// The toolbar's page name, from the navigation.
import { usePathname } from "next/navigation";
import { GROUPS, isCurrentOperator } from "./OperatorNav";

export function OperatorCrumb() {
  const pathname = usePathname();
  const entry = GROUPS.flatMap((g) => [...g.entries]).filter((e) => isCurrentOperator(pathname, e.href)).pop();
  return <span className="crumb">{entry?.label ?? "Overview"}</span>;
}
