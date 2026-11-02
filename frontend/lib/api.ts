import type { TripView } from "@/types/trip";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const CHECK_EVERY_MS = 3000;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : "The planner couldn't handle that request.";
    throw new ApiError(detail, response.status);
  }
  return response.json();
}

export async function startTrip(message: string): Promise<string> {
  const { trip_id } = await request<{ trip_id: string }>("/trips", { method: "POST", body: JSON.stringify({ message }) });
  return trip_id;
}

export async function sendMessage(tripId: string, message: string): Promise<void> {
  await request(`/trips/${tripId}/messages`, { method: "POST", body: JSON.stringify({ message }) });
}

export function readTrip(tripId: string): Promise<TripView> {
  return request<TripView>(`/trips/${tripId}`);
}

/** Shows the server's real progress until the trip finishes. If the stream drops, reading the trip still finishes it. */
export function watchTrip(tripId: string, onProgress: (text: string) => void): Promise<TripView> {
  return new Promise((resolve, reject) => {
    const events = new EventSource(`${API_URL}/trips/${tripId}/events`);
    let settled = false;

    function finish(action: () => void) {
      if (settled) return;
      settled = true;
      clearInterval(timer);
      events.close();
      action();
    }

    async function check() {
      try {
        const trip = await readTrip(tripId);
        if (trip.status !== "running") finish(() => resolve(trip));
      } catch (error) {
        finish(() => reject(error));
      }
    }

    const timer = setInterval(check, CHECK_EVERY_MS);
    events.addEventListener("progress", (event) => onProgress(JSON.parse((event as MessageEvent).data).text));
    events.addEventListener("done", check);
    events.addEventListener("failed", check);
  });
}
