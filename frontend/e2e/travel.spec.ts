import { expect, test } from "@playwright/test";
import { budgetSummary } from "../lib/money";
import { describeHop } from "../lib/travel";
import type { Budget, Hop } from "../types/trip";

const NONE: Hop = {
  public_transport: null,
  walk: null,
  taxi_backup: null,
  check_failed: false,
  distance_km: 1,
  note: null,
  reserved_minutes: null,
};
const BUS = {
  minutes: 7,
  transfers: 0,
  walking_minutes_inside: 2,
  lines: [{ mode: "BUS", line: "72", from_stop: "Louvre", to_stop: "Pont Neuf" }],
};
const WALK = { minutes: 12.7, is_estimate: false };

test("public transport and a near walk: both are offered", () => {
  const text = describeHop({ ...NONE, public_transport: BUS, walk: WALK });
  expect(text[0]).toBe("Bus 72, 7 min.");
  expect(text[1]).toBe("Or walk, about 13 min.");
  expect(text.join(" ")).not.toMatch(/taxi/i);
});

test("public transport alone: no walk or taxi is mentioned", () => {
  const text = describeHop({ ...NONE, public_transport: BUS }).join(" ");
  expect(text).toContain("Bus 72");
  expect(text).not.toMatch(/walk|taxi/i);
});

test("a walk alone: no taxi is mentioned, and an estimate is labelled", () => {
  expect(describeHop({ ...NONE, walk: WALK })).toEqual(["Walk, 13 min."]);
  expect(describeHop({ ...NONE, walk: { minutes: 5, is_estimate: true } })).toEqual(["Walk, about 5 min (estimate)."]);
});

test("a taxi only appears as a labelled backup with its reason", () => {
  const text = describeHop({ ...NONE, taxi_backup: { minutes: 8.4, reason: "No public transport route in our data." } });
  expect(text).toEqual(["Taxi (backup), about 8 min.", "No public transport route in our data."]);
});

test("transfers name each line, and modes get plain names", () => {
  const lines = [
    { mode: "SUBWAY", line: "7", from_stop: null, to_stop: null },
    { mode: "REGIONAL_RAIL", line: "C", from_stop: null, to_stop: null },
  ];
  expect(describeHop({ ...NONE, public_transport: { ...BUS, minutes: 23, lines } })[0]).toBe("Metro 7, then Train C, 23 min.");
});

test("unknown travel says so, and a date note is shown", () => {
  expect(describeHop(NONE)).toEqual(["Travel time unknown."]);
  expect(describeHop({ ...NONE, walk: WALK, note: "A normal weekday was checked." })).toContain("A normal weekday was checked.");
});

const BUDGET: Budget = { total: 90, currency: "EUR", budget: 100, over_budget: false, difference: 10, missing_costs: [], includes_estimates: true, note: "" };

test("the budget line never claims more than the numbers allow", () => {
  expect(budgetSummary(BUDGET)).toContain("Within budget.");
  expect(budgetSummary({ ...BUDGET, over_budget: true })).toContain("Over budget.");
  expect(budgetSummary({ ...BUDGET, over_budget: null })).toContain("can't be confirmed");
  expect(budgetSummary({ ...BUDGET, budget: null, over_budget: null })).not.toContain("budget:");
});
