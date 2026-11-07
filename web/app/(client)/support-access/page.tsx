// Moved into Settings (redesign 2026-11-07); the old address still works.
import { redirect } from "next/navigation";

export default function Moved() { redirect("/settings/support-access"); }
