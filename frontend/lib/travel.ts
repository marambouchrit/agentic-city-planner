import type { Hop, TransitLine } from "@/types/trip";

const MODE_NAMES: Record<string, string> = {
  SUBWAY: "Metro",
  METRO: "Metro",
  BUS: "Bus",
  COACH: "Coach",
  TRAM: "Tram",
  FERRY: "Ferry",
  CABLE_CAR: "Cable car",
  FUNICULAR: "Funicular",
  AREAL_LIFT: "Lift",
};

function minutes(value: number): string {
  return `${Math.max(1, Math.round(value))} min`;
}

function lineName(line: TransitLine): string {
  // Every other mode Transitous reports is a kind of train.
  const mode = MODE_NAMES[line.mode ?? ""] ?? "Train";
  return line.line ? `${mode} ${line.line}` : mode;
}

function rideDetail(line: TransitLine): string {
  return line.from_stop && line.to_stop ? `${lineName(line)}: ${line.from_stop} to ${line.to_stop}` : lineName(line);
}

/**
 * The traveler's transport rules, in words. Public transport comes first, a nearby walk is
 * a choice, and a taxi is only mentioned as a labelled backup when neither exists.
 * A hop's note, such as which date was checked, is shown once with the itinerary's notes.
 */
export function describeHop(hop: Hop): string[] {
  const lines: string[] = [];
  const transport = hop.public_transport;

  if (transport) {
    const names = transport.lines.map(lineName).join(", then ") || "Public transport";
    lines.push(`${names}, ${minutes(transport.minutes)}.`);
    if (hop.walk) lines.push(`Or walk, about ${minutes(hop.walk.minutes)}.`);
    const details = transport.lines.filter((line) => line.from_stop && line.to_stop);
    if (details.length) lines.push(details.map(rideDetail).join(". ") + ".");
  } else if (hop.walk) {
    lines.push(`Walk, ${hop.walk.is_estimate ? "about " : ""}${minutes(hop.walk.minutes)}${hop.walk.is_estimate ? " (estimate)" : ""}.`);
  } else if (hop.taxi_backup) {
    lines.push(`Taxi (backup), about ${minutes(hop.taxi_backup.minutes)}.`);
    lines.push(hop.taxi_backup.reason);
  } else {
    lines.push("Travel time unknown.");
  }

  return lines;
}
