// Conversations: every thread this user has, and the open one.
import { Suspense } from "react";
import { Conversations } from "@/components/ws/Conversations";

export default function ChatPage() {
  return (
    <Suspense>
      <Conversations />
    </Suspense>
  );
}
