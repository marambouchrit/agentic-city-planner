"use client";

import { useEffect, useRef, useState } from "react";
import Chat, { type ChatMessage } from "@/components/Chat";
import Itinerary from "@/components/Itinerary";
import { ApiError, readTrip, sendMessage, startTrip, watchTrip } from "@/lib/api";
import type { TripView } from "@/types/trip";

const SAVED_TRIP_KEY = "current-trip-id";
const LOST_TRIP = "Your earlier trip is gone, because trips are lost when the server restarts. Please describe it again.";

// The browser can refuse storage, for example in a private window. The page still works without it.
function savedTripId(): string | null {
  try {
    return window.localStorage.getItem(SAVED_TRIP_KEY);
  } catch {
    return null;
  }
}

function saveTripId(tripId: string | null): void {
  try {
    if (tripId) window.localStorage.setItem(SAVED_TRIP_KEY, tripId);
    else window.localStorage.removeItem(SAVED_TRIP_KEY);
  } catch {
    // The trip just won't come back after a reload.
  }
}

export default function Home() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [trip, setTrip] = useState<TripView | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const tripId = useRef<string | null>(null);
  const nextMessageId = useRef(0);
  const restored = useRef(false);

  function say(role: ChatMessage["role"], text: string, isError = false) {
    nextMessageId.current += 1;
    setMessages((previous) => [...previous, { id: nextMessageId.current, role, text, isError }]);
  }

  function forgetTrip() {
    tripId.current = null;
    saveTripId(null);
    setTrip(null);
  }

  async function follow(id: string) {
    const finished = await watchTrip(id, setProgress);
    setProgress(null);
    setTrip(finished);
    if (finished.error) say("assistant", finished.error, true);
    else if (finished.reply) say("assistant", finished.reply);
  }

  // Bring back the trip from before a reload, if the server still has it.
  useEffect(() => {
    // React can run this twice in development, and a trip must only be followed once.
    if (restored.current) return;
    restored.current = true;
    const saved = savedTripId();
    if (!saved) return;
    tripId.current = saved;
    readTrip(saved)
      .then((found) => {
        if (found.status === "running") {
          setBusy(true);
          return follow(saved).finally(() => setBusy(false));
        }
        setTrip(found);
      })
      .catch((error) => {
        if (error instanceof ApiError && error.status === 404) {
          forgetTrip();
          say("assistant", LOST_TRIP, true);
        }
      });
    // Only on the first load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSend(text: string) {
    say("user", text);
    setBusy(true);
    setProgress("Sending your request");
    try {
      if (tripId.current) {
        await sendMessage(tripId.current, text);
      } else {
        tripId.current = await startTrip(text);
        saveTripId(tripId.current);
      }
      await follow(tripId.current);
    } catch (error) {
      setProgress(null);
      if (error instanceof ApiError && error.status === 404) {
        forgetTrip();
        say("assistant", LOST_TRIP, true);
      } else {
        say("assistant", error instanceof ApiError ? error.message : "The planner can't be reached right now. Please try again.", true);
      }
    } finally {
      setBusy(false);
    }
  }

  function handleStartOver() {
    forgetTrip();
    setMessages([]);
  }

  return (
    <main className="min-h-screen bg-paper pb-16">
      <header className="mx-auto max-w-3xl px-4 pb-6 pt-10 text-center sm:px-6 sm:pt-16">
        <p className="mb-2 text-sm tracking-[0.2em] text-clay">AGENTIC CITY PLANNER</p>
        <h1 className="font-serif text-3xl text-ink sm:text-5xl">Plan a trip in one sentence.</h1>
      </header>

      <div className="mx-auto max-w-3xl space-y-8 px-4 sm:px-6">
        <Chat
          messages={messages}
          progress={progress}
          busy={busy}
          hasPlan={Boolean(trip?.schedule)}
          onSend={handleSend}
          onStartOver={tripId.current ? handleStartOver : undefined}
        />
        {trip?.schedule && <Itinerary trip={trip} refreshing={busy} />}
        {trip && <Credits trip={trip} />}
      </div>
    </main>
  );
}

function Credits({ trip }: { trip: TripView }) {
  return (
    <footer className="border-t border-ink/10 pt-4 text-xs text-ink/70">
      <ul className="space-y-1">
        {trip.credits.map((credit) => (
          <li key={credit}>{credit}</li>
        ))}
      </ul>
    </footer>
  );
}
