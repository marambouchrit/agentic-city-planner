import { describeHop } from "@/lib/travel";
import type { Stop } from "@/types/trip";

const SLOT_LABEL: Record<Stop["slot"], string> = { activity: "Visit", lunch: "Lunch", dinner: "Dinner" };
const SLOT_ACCENT: Record<Stop["slot"], string> = {
  activity: "border-clay/30 bg-clay/[0.04]",
  lunch: "border-moss/30 bg-moss/[0.04]",
  dinner: "border-ink/20 bg-ink/[0.03]",
};

export default function StopCard({ stop }: { stop: Stop }) {
  const travel = stop.hop ? describeHop(stop.hop) : [];

  return (
    <li>
      {travel.length > 0 && (
        <div data-testid="travel" className="ml-4 space-y-0.5 border-l-2 border-ink/15 py-2 pl-3 text-sm text-ink/80">
          {travel.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      )}

      <div className={`flex gap-3 rounded-xl border p-4 sm:gap-4 ${SLOT_ACCENT[stop.slot]}`}>
        <div data-testid="stop-time" className="w-12 shrink-0 pt-0.5 text-sm font-medium text-ink">{stop.start}</div>
        <div className="min-w-0 flex-1">
          <h3 className="break-words font-medium text-ink">{stop.name}</h3>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink/70">
            <span>{SLOT_LABEL[stop.slot]}</span>
            <span>
              {stop.duration_minutes} min{stop.duration_is_estimate ? " (estimate)" : ""}
            </span>
            {stop.place_id?.startsWith("foursquare:") && <span>Powered by Foursquare</span>}
            {stop.latitude !== null && stop.longitude !== null && (
              // A plain link: nothing goes to Google unless the traveler opens it.
              <a
                href={`https://www.google.com/maps/search/?api=1&query=${stop.latitude},${stop.longitude}`}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`Open ${stop.name} in Google Maps`}
                className="text-clay underline underline-offset-2 hover:text-ink"
              >
                Open in Google Maps
              </a>
            )}
          </div>
        </div>
      </div>
    </li>
  );
}
