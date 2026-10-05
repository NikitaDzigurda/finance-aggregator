import { expect, test } from "@playwright/test";
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const apiUrl = process.env.FINANCE_API_URL ?? "http://127.0.0.1:8011";
const fixtureDir = fileURLToPath(
  new URL("../../docs/fixtures/manual_qa", import.meta.url),
);

test("Bybit account creation, long filenames and four-file confirmation", async ({
  page,
  request,
}, testInfo) => {
  const response = await request.post(`${apiUrl}/api/v1/portfolios`, {
    data: { name: `QA bundle ${Date.now()}`, base_currency: "USD" },
  });
  expect(response.ok()).toBeTruthy();
  const portfolio = await response.json();
  await page.goto(`/p/${portfolio.id}/imports/new`);
  await page.getByRole("button", { name: "Bybit Spot" }).click();
  await page.getByLabel("Файлы отчёта").setInputFiles(
    readdirSync(fixtureDir)
      .filter((name) => name.endsWith(".csv"))
      .map((name) => join(fixtureDir, name)),
  );
  await expect(page.getByText("Выбрано файлов: 4")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Загрузить и проверить" }),
  ).toBeDisabled();
  await expect(
    page.getByText("Сначала создайте CEX-счёт для Bybit", { exact: false }),
  ).toBeVisible();
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    if (width < 761) {
      await expect
        .poll(() =>
          page
            .locator(".sidebar")
            .evaluate((el) => el.getBoundingClientRect().right),
        )
        .toBeLessThanOrEqual(0);
      const headerOverlap = await page.evaluate(() => {
        const a = document
          .querySelector(".topbar__portfolio-group")!
          .getBoundingClientRect();
        const b = document
          .querySelector(".topbar__tools")!
          .getBoundingClientRect();
        return Math.min(a.bottom, b.bottom) > Math.max(a.top, b.top);
      });
      expect(headerOverlap).toBe(false);
    }
    await expect(page.locator(".document-checklist li")).toHaveCount(4);
    const geometry = await page.evaluate(() => {
      const a = document.querySelector(".dropzone")!.getBoundingClientRect();
      const b = document
        .querySelector(".upload-files")!
        .getBoundingClientRect();
      const c = document
        .querySelector(".upload-account")!
        .getBoundingClientRect();
      return {
        overflow: document.documentElement.scrollWidth - innerWidth,
        overlap:
          (Math.min(a.right, b.right) > Math.max(a.left, b.left) &&
            Math.min(a.bottom, b.bottom) > Math.max(a.top, b.top)) ||
          (Math.min(a.right, c.right) > Math.max(a.left, c.left) &&
            Math.min(a.bottom, c.bottom) > Math.max(a.top, c.top)),
      };
    });
    expect(geometry).toEqual({ overflow: 0, overlap: false });
    await page.screenshot({
      path: testInfo.outputPath(`import-${width}.png`),
      fullPage: true,
    });
  }
  await page.getByRole("button", { name: "Создать счёт", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Загрузить и проверить" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Загрузить и проверить" }).click();
  await expect(
    page.getByRole("heading", { name: "Проверка импорта" }),
  ).toBeVisible({ timeout: 45000 });
  await expect(page.getByText("Готово к подтверждению")).toBeVisible();
  await page.getByRole("button", { name: "Подтвердить и пересчитать" }).click();
  await page.waitForURL("**/overview");
  await page.goto(`/p/${portfolio.id}/data-quality`);
  await expect(
    page.getByRole("heading", { name: "Курс доллара · Банк России" }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("combobox", { name: "Инструмент", exact: true })
      .locator("option"),
  ).toHaveCount(3);
  const options = await page
    .getByRole("combobox", { name: "Инструмент", exact: true })
    .locator("option")
    .allTextContents();
  expect(options).toHaveLength(3);
  expect(options.every((text) => /QAX|QAY|USDT/.test(text))).toBeTruthy();
  await expect(page.locator(".manual-fx-panel")).not.toHaveAttribute(
    "open",
    "",
  );
  await page.screenshot({
    path: testInfo.outputPath("quality-mobile.png"),
    fullPage: true,
  });
});
