import { useCallback, useState } from "react";

/**
 * Busy/error bookkeeping for API calls. `run` returns the action's value, or
 * `undefined` when it failed (the error is kept for <ErrorBox>), so callers can
 * write `setResult((await run("price", () => api.price(body))) ?? null)`.
 */
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const run = useCallback(async <T,>(label: string, action: () => Promise<T>): Promise<T | undefined> => {
    setBusy(label);
    setError(null);
    try {
      return await action();
    } catch (e) {
      setError(e);
      return undefined;
    } finally {
      setBusy(null);
    }
  }, []);

  return { busy, error, setError, run };
}
