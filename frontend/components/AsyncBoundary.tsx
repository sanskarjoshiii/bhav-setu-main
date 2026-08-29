"use client";

/**
 * Loading and error states, so every page fails the same readable way.
 *
 * The error copy is deliberately the backend's own sentence rather than a
 * generic "Something went wrong". If the API says a crop has too little
 * history, that is the most useful thing we can put on screen — the farmer
 * learns something instead of staring at an empty chart.
 */

import { AlertTriangle, Loader2, WifiOff } from "lucide-react";
import type { ApiError } from "@/lib/api";

export function LoadingState({ label = "Loading…", rows = 3 }: {
  label?: string;
  rows?: number;
}) {
  return (
    <div className="animate-pulse space-y-3" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        {label}
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-12 rounded-lg bg-slate-100 dark:bg-slate-800" />
      ))}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const offline = error.status === 0;
  return (
    <div
      role="alert"
      className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-700/60 dark:bg-amber-950/30"
    >
      <div className="flex items-start gap-3">
        {offline ? (
          <WifiOff className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden />
        ) : (
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden />
        )}
        <div className="space-y-1">
          <p className="font-medium text-amber-900 dark:text-amber-200">
            {offline ? "Cannot reach the server" : "This data is not available"}
          </p>
          <p className="text-amber-800 dark:text-amber-300">{error.message}</p>
          {error.hint && (
            <p className="text-xs text-amber-700 dark:text-amber-400">{error.hint}</p>
          )}
          {offline && (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              Start it with <code className="font-mono">make api</code>.
            </p>
          )}
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-2 rounded-md border border-amber-400 px-3 py-1 text-xs font-medium text-amber-900 hover:bg-amber-100 dark:text-amber-200 dark:hover:bg-amber-900/40"
            >
              Try again
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500 dark:border-slate-700">
      {message}
    </div>
  );
}

/** Renders children only once data has arrived. */
export function AsyncBoundary<T>({
  state,
  children,
  loadingLabel,
  emptyMessage = "Nothing to show yet.",
  rows,
}: {
  state: { data: T | null; loading: boolean; error: ApiError | null; reload: () => void };
  children: (data: T) => React.ReactNode;
  loadingLabel?: string;
  emptyMessage?: string;
  rows?: number;
}) {
  if (state.loading) return <LoadingState label={loadingLabel} rows={rows} />;
  if (state.error) return <ErrorState error={state.error} onRetry={state.reload} />;
  if (
    state.data === null ||
    (Array.isArray(state.data) && state.data.length === 0)
  ) {
    return <EmptyState message={emptyMessage} />;
  }
  return <>{children(state.data)}</>;
}
