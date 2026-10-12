"use client";
// The composer under an account. The answer stream cannot yet be scoped to one account
// (docs/FRONTEND_BACKEND_GAPS.md G-03), so the question goes to the workspace as asked.
import { Conversation } from "./Conversation";
import { m } from "@/lib/messages";

export function AccountAsk({ name }: { name: string }) {
  return (
    <div className="section">
      <Conversation placeholder={m.composer.account(name)} />
    </div>
  );
}
