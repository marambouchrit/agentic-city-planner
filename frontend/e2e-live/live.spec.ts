import { writeFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const OUT = "test-results/live";

async function ask(page: Page, message: string) {
  await page.getByRole("textbox").fill(message);
  await page.getByRole("button", { name: "Send" }).click();
}

// Every stop of every day: day, time, slot, name, and the travel text shown before it.
async function everyStop(page: Page) {
  const all: { day: string; time: string; slot: string; name: string; travel: string }[] = [];
  const days = await page.getByRole("group", { name: "Choose a day" }).getByRole("button").allInnerTexts();
  for (const day of days) {
    await page.getByRole("button", { name: day, exact: true }).click();
    const stops = await page.locator("section[aria-label='Your itinerary'] ol > li").evaluateAll((items) =>
      items.map((item) => ({
        time: item.querySelector('[data-testid="stop-time"]')?.textContent ?? "",
        name: item.querySelector("h3")?.textContent ?? "",
        slot: item.querySelector("h3")?.nextElementSibling?.firstElementChild?.textContent ?? "",
        travel: item.querySelector('[data-testid="travel"]')?.textContent ?? "",
      })),
    );
    for (const stop of stops) all.push({ day, ...stop });
  }
  return all;
}

function checks(stops: Awaited<ReturnType<typeof everyStop>>) {
  const byDay: Record<string, typeof stops> = {};
  for (const stop of stops) (byDay[stop.day] ??= []).push(stop);
  return {
    stops: stops.length,
    visits: stops.filter((s) => s.slot === "Visit").length,
    taxiHops: stops.filter((s) => /taxi/i.test(s.travel)).map((s) => `${s.day} ${s.name}: ${s.travel}`),
    // A visit after dinner would break the rule that dinner ends the day.
    visitAfterDinner: Object.entries(byDay).flatMap(([day, list]) => {
      const dinner = list.findIndex((s) => s.slot === "Dinner");
      return dinner >= 0 ? list.slice(dinner + 1).filter((s) => s.slot === "Visit").map((s) => `${day} ${s.name}`) : [];
    }),
    meals: Object.fromEntries(Object.entries(byDay).map(([day, list]) => [day, list.filter((s) => s.slot !== "Visit").map((s) => s.slot)])),
  };
}

test("live: a 3-day Paris trip, then a change", async ({ page }) => {
  const log: Record<string, unknown> = {};
  const progress: string[] = [];
  const started = Date.now();
  page.on("console", (message) => message.type() === "error" && progress.push(`console error: ${message.text()}`));

  await page.goto("/");
  await ask(page, "3 days in Paris with 400 euros, I love art and history");
  // Record each real progress message as it appears.
  const watcher = setInterval(async () => {
    const text = await page.locator("[aria-live=polite]").innerText().catch(() => "");
    if (text && !progress[progress.length - 1]?.endsWith(text)) progress.push(`${Math.round((Date.now() - started) / 1000)}s ${text}`);
  }, 1000);
  await expect(page.getByRole("heading", { name: "Paris", exact: true })).toBeVisible({ timeout: 20 * 60_000 });
  log.firstSeconds = Math.round((Date.now() - started) / 1000);
  await page.screenshot({ path: `${OUT}/1-trip.png`, fullPage: true });

  const before = await everyStop(page);
  log.firstChecks = checks(before);
  log.firstStops = before;
  log.firstBanner = await page.locator("section[aria-label='Your itinerary'] > div").nth(1).innerText();
  log.firstChat = await page.getByRole("region", { name: "Chat with the planner" }).locator("ul").innerText();

  const target = before.find((s) => s.name === "Louvre") ?? before.find((s) => s.slot === "Visit")!;
  log.removed = target.name;
  const changeStarted = Date.now();
  await ask(page, `I don't want the ${target.name}, replace it with something else`);
  await expect(page.getByRole("textbox")).toBeEnabled({ timeout: 20 * 60_000 });
  log.changeSeconds = Math.round((Date.now() - changeStarted) / 1000);
  clearInterval(watcher);
  await page.screenshot({ path: `${OUT}/2-change.png`, fullPage: true });

  const after = await everyStop(page);
  log.changeChecks = checks(after);
  log.changeStops = after;
  log.changeBanner = await page.locator("section[aria-label='Your itinerary'] > div").nth(1).innerText();
  log.changeChat = await page.getByRole("region", { name: "Chat with the planner" }).locator("ul").innerText();
  const key = (s: { day: string; time: string; name: string }) => `${s.day} ${s.time} ${s.name}`;
  log.keptUnchanged = before.filter((s) => s.name !== target.name).every((s) => after.some((a) => key(a) === key(s)));
  log.added = after.filter((a) => !before.some((b) => b.name === a.name)).map(key);
  log.moved = before.filter((s) => s.name !== target.name && !after.some((a) => key(a) === key(s))).map(key);
  log.progress = progress;

  await page.setViewportSize({ width: 412, height: 915 });
  await page.screenshot({ path: `${OUT}/3-phone.png`, fullPage: true });
  log.sidewaysOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

  writeFileSync(`${OUT}/summary.json`, JSON.stringify(log, null, 2));
});
