import { budgetSummary } from "@/lib/money";
import type { TripView } from "@/types/trip";

export default function ReviewStatus({ trip }: { trip: TripView }) {
  return (
    <div className="space-y-3">
      {trip.showing_previous_plan && (
        <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950">
          Your change couldn&apos;t be finished, so this is your previous plan, unchanged.
        </p>
      )}

      {trip.review_status === "approved" && (
        <p className="rounded-lg border border-moss/40 bg-moss/10 px-3 py-2 text-sm text-moss">
          Checked by the Reviewer: no rule errors, and within budget by its estimates.
        </p>
      )}
      {trip.review_status === "not_approved" && (
        <div role="alert" className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
          <p className="font-medium">This plan was not approved. It still has problems:</p>
          <ul className="mt-1 space-y-1">
            {trip.problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      )}
      {trip.review_status === "not_reviewed" && (
        <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950">
          This plan was not reviewed, so its rules and budget weren&apos;t checked.
        </p>
      )}

      {trip.budget && (
        <details className="rounded-lg border border-ink/15 bg-white/60 px-3 py-2 text-sm text-ink">
          <summary className="cursor-pointer">{budgetSummary(trip.budget)}</summary>
          <ul className="mt-2 space-y-1">
            {trip.costs.map((item) => (
              <li key={item.name} className="flex justify-between gap-4">
                <span className="min-w-0 break-words">{item.name}</span>
                <span className="shrink-0">{item.amount === null ? "Unknown" : `${item.amount} ${trip.budget!.currency}`}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-ink/70">{trip.budget.note}</p>
        </details>
      )}
    </div>
  );
}
