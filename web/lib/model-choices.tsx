"use client";
// The model menus' data, loaded ONCE per page view by the workspace layout (server side, in the
// same round as the user and the chats) and shared with every screen: Ask, New research, Refresh
// and Model preferences all start from the user's saved models at first paint, with no extra
// request. Saving a preference updates the shared copy, so the next screen already has it.
import { createContext, useContext, useState } from "react";
import type { ModelChoices } from "@/lib/gen/api.gen";

type Shared = { choices: ModelChoices | null; setChoices: (c: ModelChoices) => void };

const Ctx = createContext<Shared | null>(null);

export function ModelChoicesProvider({ initial, children }: { initial: ModelChoices | null; children: React.ReactNode }) {
  const [choices, setChoices] = useState<ModelChoices | null>(initial);
  return <Ctx.Provider value={{ choices, setChoices }}>{children}</Ctx.Provider>;
}

/** The shared model data, or null outside the workspace shell (tests, the sign-in pages). */
export function useSharedModelChoices(): Shared | null {
  return useContext(Ctx);
}
