import { useCallback, useState } from "react";

/** Setup controls that survive a reload.
 *
 * **`localStorage`, not `sessionStorage`, and the difference is deliberate.**
 * The open run's id is session-scoped because a run belongs to the tab that
 * opened it — two tabs watching one run would fight over it. What is stored
 * here is the opposite kind of thing: which model you are testing, which reader
 * and judge you chose, which part of the list you are working through. Those
 * are *preferences*, they are the same in every tab, and re-picking three
 * dropdowns after every reload is the kind of friction that makes a tool feel
 * hostile.
 *
 * Nothing here is part of a measurement. A stored verdict carries its own
 * instrument on the row, so restoring a reader into the bar changes what the
 * *next* run will use and says nothing about any run already recorded.
 *
 * Every access is wrapped: `localStorage` throws outright in some privacy
 * modes, and a page that cannot remember a dropdown must still render.
 */
const PREFIX = "polyglot.";

export function readStored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    if (raw === null) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function writeStored(key: string, value: unknown): void {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch { /* a page that cannot remember still has to work */ }
}

/** `useState`, remembered under `key`.
 *
 * Takes a value rather than an updater function, which is what every caller
 * here passes — an updater would have to be resolved before it could be
 * written, and nothing in these bars needs one.
 */
export function usePersisted<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => readStored(key, initial));
  const set = useCallback((next: T) => {
    setValue(next);
    writeStored(key, next);
  }, [key]);
  return [value, set] as const;
}
