// True after hydration, false on the server and in the first client render, so text that depends
// on the browser (its time zone, its clock) never differs between the two renders.
import { useSyncExternalStore } from "react";

const subscribe = () => () => {};

export function useIsClient(): boolean {
  return useSyncExternalStore(subscribe, () => true, () => false);
}
