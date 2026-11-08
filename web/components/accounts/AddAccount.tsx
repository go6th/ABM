"use client";
// Add an account: research a new company (one of the user's researches, confirmed first).
// Owner order 2026-11-08: the "Ready-made research shared with your team" and "Analyze your own
// file" sections are gone from this page; importing a file is Accounts > Import a file.
// Accounts are not capped (owner decision 2026-10-14: researches and chats are).
import type { ResearchEstimate } from "@/lib/gen/api.gen";
import { ResearchCompany } from "./ResearchCompany";

export function AddAccount({ estimate }: { estimate: ResearchEstimate | null }) {
  return (
    <section className="card pad-lg stack-14" aria-labelledby="add-research">
      <div className="stack-4">
        <h2 id="add-research" className="h-sm">Research a new company</h2>
        <p className="mut sm-text">Researched from public sources. You are told when it is ready.</p>
      </div>
      <ResearchCompany estimate={estimate} idPrefix="add" />
    </section>
  );
}
