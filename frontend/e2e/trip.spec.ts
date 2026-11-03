import { expect, test, type Page } from "@playwright/test";

async function ask(page: Page, message: string) {
  await page.getByRole("textbox").fill(message);
  await page.getByRole("button", { name: "Send" }).click();
}

function stops(page: Page) {
  return page.getByRole("region", { name: "Your itinerary" }).getByRole("listitem");
}

// Each stop on the day on show, as its time and name.
function stopSummaries(page: Page): Promise<string[]> {
  return page
    .getByRole("region", { name: "Your itinerary" })
    .locator("ol > li")
    .evaluateAll((items) =>
      items.map((item) => `${item.querySelector('[data-testid="stop-time"]')?.textContent} ${item.querySelector("h3")?.textContent}`),
    );
}

// Every stop of the trip, because the solver chooses which day each place is on.
async function everyStop(page: Page): Promise<string[]> {
  const all: string[] = [];
  for (const day of ["Day 1", "Day 2"]) {
    await page.getByRole("button", { name: day }).click();
    for (const stop of await stopSummaries(page)) all.push(`${day} ${stop}`);
  }
  return all;
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
});

test("one sentence gives a checked trip, with real progress on the way", async ({ page }) => {
  await ask(page, "2 days in Paris with 500 euros");
  await expect(page.getByText("Finding and verifying places", { exact: false })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Paris", exact: true })).toBeVisible({ timeout: 45_000 });
  await expect(page.getByText("Checked by the Reviewer")).toBeVisible();
  await expect(page.getByText("Estimated cost for one traveler", { exact: false })).toBeVisible();
  await expect(page.getByText("Opening hours were not checked", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("Powered by Foursquare").first()).toBeVisible();
  await expect(page.getByText("Restaurants are powered by Foursquare.")).toBeVisible();
  await expect(page.getByText("Transitous", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("No date was given, so a normal weekday was checked.")).toHaveCount(1);
});

test("public transport comes first, a near walk is a choice, and no taxi is mentioned", async ({ page }) => {
  await ask(page, "2 days in Paris with 500 euros");
  await expect(page.getByRole("heading", { name: "Paris", exact: true })).toBeVisible({ timeout: 45_000 });
  for (const day of ["Day 1", "Day 2"]) {
    await page.getByRole("button", { name: day }).click();
    await expect(page.getByTestId("travel").first()).toBeVisible();
    await expect(page.getByRole("region", { name: "Your itinerary" })).not.toContainText(/taxi/i);
  }
  await expect(page.getByTestId("travel").filter({ hasText: "Or walk, about" }).first()).toBeVisible();
  await expect(page.getByTestId("travel").first()).toContainText(/Metro 1, \d+ min/);
});

test("a city with no public transport data shows honest walks and a labelled taxi backup", async ({ page }) => {
  await ask(page, "2 days in Tunis with 500 euros");
  await expect(page.getByRole("heading", { name: "Tunis", exact: true })).toBeVisible({ timeout: 45_000 });
  const travel = page.getByTestId("travel");
  await expect(travel.filter({ hasText: "Taxi (backup)" }).first()).toContainText("No public transport route in our data");
  await expect(travel.filter({ hasText: /Metro|Bus|Or walk/ })).toHaveCount(0);
});

test("a change keeps the other stops at the same time", async ({ page }) => {
  await ask(page, "2 days in Paris with 500 euros");
  await expect(page.getByRole("heading", { name: "Paris", exact: true })).toBeVisible({ timeout: 45_000 });
  const before = await everyStop(page);
  expect(before.join(" ")).toContain("Louvre");

  await ask(page, "Replace the Louvre with something else");
  await expect(page.getByText("Changing your plan and keeping your other stops.")).toBeVisible({ timeout: 45_000 });
  const after = await everyStop(page);
  expect(after.join(" ")).not.toContain("Louvre");
  expect(after).toEqual(expect.arrayContaining(before.filter((stop) => !stop.includes("Louvre"))));
});

test("no city gets a question, with no plan on screen", async ({ page }) => {
  await ask(page, "I want a nice holiday");
  await expect(page.getByText("Which city would you like to visit?")).toBeVisible({ timeout: 45_000 });
  await expect(page.getByRole("region", { name: "Your itinerary" })).toHaveCount(0);
});

test("a plan still over budget is clearly not approved", async ({ page }) => {
  await ask(page, "2 days in Paris, very cheap");
  await expect(page.getByText("This plan was not approved", { exact: false })).toBeVisible({ timeout: 45_000 });
  await expect(page.locator("summary")).toContainText("Over budget.");
  await expect(page.getByText("Checked by the Reviewer")).toHaveCount(0);
});

test("a failed first trip gives a plain message with no internal details", async ({ page }) => {
  await ask(page, "2 days in Paris, crash now");
  await expect(page.getByText("The plan could not be finished right now.")).toBeVisible({ timeout: 45_000 });
  await expect(page.locator("body")).not.toContainText("secret");
});

test("a failed change keeps showing the previous plan", async ({ page }) => {
  await ask(page, "2 days in Paris with 500 euros");
  await expect(page.getByRole("heading", { name: "Paris", exact: true })).toBeVisible({ timeout: 45_000 });
  const before = await stops(page).allInnerTexts();
  await ask(page, "Break during this change");
  await expect(page.getByText("so this is your previous plan, unchanged", { exact: false })).toBeVisible({ timeout: 45_000 });
  await expect(page.getByText("Your previous plan is unchanged.", { exact: false })).toBeVisible();
  expect(await stops(page).allInnerTexts()).toEqual(before);
});

test("a reload brings the trip back", async ({ page }) => {
  await ask(page, "2 days in Paris with 500 euros");
  await expect(page.getByRole("heading", { name: "Paris", exact: true })).toBeVisible({ timeout: 45_000 });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Paris", exact: true })).toBeVisible();
});

test("screenshots for a person to read", async ({ page }, info) => {
  await ask(page, "2 days in Paris with 500 euros");
  await expect(page.getByRole("heading", { name: "Paris", exact: true })).toBeVisible({ timeout: 45_000 });
  await page.screenshot({ path: `test-results/paris-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Start a new trip" }).click();
  await ask(page, "2 days in Tunis with 500 euros");
  await expect(page.getByRole("heading", { name: "Tunis", exact: true })).toBeVisible({ timeout: 45_000 });
  await page.screenshot({ path: `test-results/tunis-${info.project.name}.png`, fullPage: true });
});

test("a trip lost in a server restart is explained", async ({ page }) => {
  await page.evaluate(() => window.localStorage.setItem("current-trip-id", "gone"));
  await page.reload();
  await expect(page.getByText("trips are lost when the server restarts", { exact: false })).toBeVisible();
  await ask(page, "I want a nice holiday");
  await expect(page.getByText("Which city would you like to visit?")).toBeVisible({ timeout: 45_000 });
});

test("the API being down gives a plain message", async ({ page }) => {
  await page.route("**/trips", (route) => route.abort());
  await ask(page, "2 days in Paris with 500 euros");
  await expect(page.getByText("The planner can't be reached right now", { exact: false })).toBeVisible();
});

test("the daily limit message from the API is shown", async ({ page }) => {
  await page.route("**/trips", (route) =>
    route.fulfill({ status: 429, contentType: "application/json", json: { detail: "The daily planning limit was reached. Please try again tomorrow." } }),
  );
  await ask(page, "2 days in Paris with 500 euros");
  await expect(page.getByText("The daily planning limit was reached.", { exact: false })).toBeVisible();
});

test("start over clears the trip", async ({ page }) => {
  await ask(page, "2 days in Paris with 500 euros");
  await expect(page.getByRole("heading", { name: "Paris", exact: true })).toBeVisible({ timeout: 45_000 });
  await page.getByRole("button", { name: "Start a new trip" }).click();
  await expect(page.getByRole("region", { name: "Your itinerary" })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("region", { name: "Your itinerary" })).toHaveCount(0);
});

test("a trip can be planned by keyboard alone", async ({ page }) => {
  await page.getByRole("textbox").focus();
  await page.keyboard.type("2 days in Paris with 500 euros");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Paris", exact: true })).toBeVisible({ timeout: 45_000 });
  await page.getByRole("button", { name: "Day 2" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Day 2" })).toHaveAttribute("aria-pressed", "true");
});

test("the page fits the screen with no sideways scrolling", async ({ page }) => {
  await ask(page, "2 days in Paris with 500 euros");
  await expect(page.getByRole("heading", { name: "Paris", exact: true })).toBeVisible({ timeout: 45_000 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
