// The shapes returned by city_planner/api.py's trip_view.

export interface TransitLine {
  mode: string | null;
  line: string | null;
  from_stop: string | null;
  to_stop: string | null;
}

export interface Hop {
  public_transport: {
    minutes: number;
    transfers: number;
    lines: TransitLine[];
    walking_minutes_inside: number | null;
  } | null;
  walk: { minutes: number; is_estimate: boolean } | null;
  taxi_backup: { minutes: number; reason: string } | null;
  check_failed: boolean;
  distance_km: number | null;
  note: string | null;
  reserved_minutes: number | null;
}

export interface Stop {
  place_id: string | null;
  name: string;
  slot: "activity" | "lunch" | "dinner";
  start: string;
  duration_minutes: number;
  duration_is_estimate: boolean;
  latitude: number | null;
  longitude: number | null;
  hop: Hop | null;
}

export interface Budget {
  total: number;
  currency: string;
  budget: number | null;
  over_budget: boolean | null;
  difference: number | null;
  missing_costs: string[];
  includes_estimates: boolean;
  note: string;
}

export interface TripView {
  trip_id: string;
  status: "running" | "done" | "failed";
  reply: string | null;
  error: string | null;
  showing_previous_plan: boolean;
  request: { city: string; days: number } | null;
  schedule: {
    status: "ok" | "partial" | "failed";
    days: { day_number: number; stops: Stop[] }[];
    notes: string[];
  } | null;
  explanation: string | null;
  review_status: "approved" | "not_approved" | "not_reviewed" | null;
  budget: Budget | null;
  costs: { name: string; amount: number | null; is_estimate: boolean }[];
  problems: string[];
  notes: string[];
  labels: string[];
  credits: string[];
}
