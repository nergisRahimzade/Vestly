import { expect, test, type Page } from "@playwright/test";

// The sixty-second moment, walked in a real browser against a real backend (company world, October 2026, scripted model).
test.describe.configure({ mode: "serial" });

async function signInAs(page: Page, handle: string) {
  await page.goto("/");
  if (await page.locator("[data-me]").count()) await page.getByRole("button", { name: "Sign out" }).click();
  await page.locator(`button[data-handle="${handle}"]`).click();
  await expect(page.locator(`[data-me="${handle}"]`)).toBeVisible();
}

test("the login picker offers people, never Vestly, and the token decides the role", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("button[data-handle]").first()).toBeVisible();
  await expect(page.locator('button[data-handle="dana.ruiz"]')).toBeVisible();
  await expect(page.locator('button[data-handle="vestly.payroll"]')).toHaveCount(0);
  await expect(page.locator('button[data-handle="vestly.equity"]')).toHaveCount(0);
  await signInAs(page, "lee.park");
  await expect(page.locator("[data-me]")).toContainText("hr ops");
  // HR ops has no audit rights, so the case list and the evidence log are not offered.
  await expect(page.getByRole("link", { name: "Cases" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Evidence" })).toHaveCount(0);
});

test("the moment: held, defended, blocked everywhere, and only the missing lines paid", async ({ page }) => {
  const started = Date.now();
  await signInAs(page, "dana.ruiz");
  await page.goto("/#/moment");

  // 1. The October close is drafted; Maya's emailed bank change is held, citing the March precedent and the email.
  await page.getByRole("button", { name: "Run the close" }).click();
  await expect(page.locator('[data-beat-done="1"]')).toBeVisible({ timeout: 120_000 });
  const maya = page.locator('[data-maya-outcome="held"]');
  await expect(maya).toContainText("Maya Chen");
  await expect(maya).toContainText("New acount: 9911882277");
  await expect(maya).toContainText("P-2026-03-001");
  await expect(maya).toContainText("1002003001");

  // 2. The controller pushes back; the answer comes from the record and the money does not move.
  await page.getByRole("button", { name: "Push back as the controller" }).click();
  await expect(page.locator('[data-beat-done="2"]')).toBeVisible();
  const answer = page.locator('#beat-2 [data-blocked="false"]');
  await expect(answer).toContainText("The decision stands");
  await expect(answer).toContainText("BNK-1");
  await expect(answer).toContainText("P-2026-03-001");
  await expect(page.locator('[data-money-moved="0"]')).toContainText("none requested, none paid");

  // 3. HR's call-back shows fraud; Vestly proposes the block and waits; the controller confirms it.
  await page.getByRole("button", { name: "HR calls Maya back" }).click();
  await expect(page.locator('[data-beat-done="3"]')).toBeVisible({ timeout: 120_000 });
  await expect(page.locator("#beat-3")).toContainText("reads it as “declined”");
  await expect(page.locator("#beat-3 .run")).toContainText("waiting for dana.ruiz to confirm it");
  const block = page.locator("#beat-3 [data-precedent]");
  await expect(block).toContainText('{"account":"9911882277"}');

  // 4. Treasury releases only the two unconfirmed legitimate lines; funding log, bank and ledger agree to the cent.
  await page.getByRole("button", { name: "Treasury releases" }).click();
  await expect(page.locator('[data-beat-done="4"]')).toBeVisible({ timeout: 120_000 });
  const recon = page.locator('[data-reconciliation="agrees"]');
  await expect(recon).toContainText("PAY-2026-10-NL-US-002");
  await expect(recon).toContainText("PAY-2026-10-NL-US-005");
  await expect(recon).not.toContainText("PAY-2026-10-NL-US-001");
  await expect(page.locator("#beat-4")).toContainText("unbroken");
  console.log(`The moment took ${Math.round((Date.now() - started) / 1000)} s in the browser (close included).`);
});

test("the record behind it: case page, stored email, evidence chain, precedents, rates and the proof", async ({ page }) => {
  await signInAs(page, "riley.auditor");
  await page.goto("/#/cases");
  const row = page.locator('tr[data-case^="bank_detail_change:NL-US-001"]');
  await expect(row).toBeVisible();
  await row.click();
  const detail = page.locator('[data-case-detail^="bank_detail_change:NL-US-001"]');
  await expect(detail).toContainText("1 · trusted");
  await page.getByRole("button", { name: "Open the stored document" }).click();
  await expect(detail.locator("mark")).toHaveText("New acount: 9911882277");
  await expect(detail.locator('[data-employee="NL-US-001"]')).toContainText("2026-10");

  await page.goto("/#/evidence");
  await page.getByRole("button", { name: "Verify the hash chain" }).click();
  await expect(page.locator('[data-chain="ok"]')).toBeVisible();

  await page.goto("/#/policy");
  await expect(page.locator('[data-precedent="P-2026-03-001"]')).toBeVisible();
  await expect(page.locator("[data-precedents]")).toContainText("9911882277");

  await page.goto("/#/rates");
  await expect(page.locator("[data-rates]")).toContainText("2026-10-31");

  await page.goto("/#/scorer");
  await expect(page.getByText("python -m app.eval.score --world company")).toBeVisible();
  await expect(page.locator("[data-scored]")).toContainText("A01");
});

test("a stranger is refused at every door", async ({ page }) => {
  await signInAs(page, "stranger");
  await page.goto("/#/inbox");
  await expect(page.getByText("Nothing waits for you to approve.")).toBeVisible();
  await page.goto("/#/history");
  await expect(page.getByText("never one person's pay")).toBeVisible();
});
