"use client";

import { useState } from "react";
import ReviewStatus from "@/components/ReviewStatus";
import StopCard from "@/components/StopCard";
import type { TripView } from "@/types/trip";

export default function Itinerary({ trip, refreshing }: { trip: TripView; refreshing: boolean }) {
  const schedule = trip.schedule!;
  const [activeDay, setActiveDay] = useState(1);
  // A change can shorten the trip, so the chosen day may no longer exist.
  const day = schedule.days.find((candidate) => candidate.day_number === activeDay) ?? schedule.days[0];

  return (
    <section aria-label="Your itinerary" className={`space-y-5 transition-opacity ${refreshing ? "opacity-60" : ""}`}>
      <div>
        <p className="text-sm text-clay">Your itinerary</p>
        <h2 className="font-serif text-3xl text-ink">{trip.request?.city}</h2>
      </div>

      <ReviewStatus trip={trip} />

      {schedule.days.length > 1 && (
        <div role="group" aria-label="Choose a day" className="flex flex-wrap gap-2 border-b border-ink/10 pb-3">
          {schedule.days.map((candidate) => (
            <button
              key={candidate.day_number}
              aria-pressed={candidate.day_number === day.day_number}
              onClick={() => setActiveDay(candidate.day_number)}
              className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                candidate.day_number === day.day_number ? "bg-ink text-paper" : "text-ink/80 hover:bg-ink/5"
              }`}
            >
              Day {candidate.day_number}
            </button>
          ))}
        </div>
      )}

      {day.stops.length > 0 ? (
        <ol className="space-y-1">
          {day.stops.map((stop, index) => (
            <StopCard key={`${stop.place_id ?? stop.slot}-${index}`} stop={stop} />
          ))}
        </ol>
      ) : (
        <p className="rounded-xl border border-dashed border-ink/30 p-6 text-center text-sm text-ink/70">
          No stops could be planned for this day. See the notes below.
        </p>
      )}

      <Notes trip={trip} />
    </section>
  );
}

function Notes({ trip }: { trip: TripView }) {
  // The labels below already say this, so it isn't repeated in the notes.
  // Travel notes, such as which date was checked, are usually the same for every hop, so each is shown once.
  const travelNotes = trip.schedule!.days.flatMap((day) => day.stops.map((stop) => stop.hop?.note ?? "")).filter(Boolean);
  const notes = [...new Set([...trip.notes, ...trip.schedule!.notes, ...travelNotes])].filter((note) => !trip.labels.includes(note));
  return (
    <div className="space-y-3 rounded-xl border border-clay/30 bg-clay/5 p-4 text-sm text-ink/90">
      {notes.length > 0 && (
        <div>
          <h3 className="font-medium text-clay">Notes</h3>
          <ul className="mt-1 space-y-1">
            {notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      )}
      <div>
        <h3 className="font-medium text-clay">Please keep in mind</h3>
        <ul className="mt-1 space-y-1">
          {trip.labels.map((label) => (
            <li key={label}>{label}</li>
          ))}
        </ul>
      </div>
      {trip.explanation && (
        <p>
          <span className="font-medium">The AI&apos;s opinion, not verified data: </span>
          {trip.explanation}
        </p>
      )}
    </div>
  );
}
