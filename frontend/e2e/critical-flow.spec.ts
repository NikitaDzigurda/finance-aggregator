import { expect, test, type APIRequestContext } from "@playwright/test";
import { fileURLToPath } from "node:url";

const apiUrl = process.env.FINANCE_API_URL ?? "http://127.0.0.1:8011";
const tbankFixture = fileURLToPath(
  new URL(
    "../../docs/fixtures/tbank_broker_report_synthetic_v1.xlsx",
    import.meta.url,
  ),
);

type TestPortfolio = {
  id: string;
  name: string;
  base_currency: string;
};

async function getOrCreateTestPortfolio(
  request: APIRequestContext,
  name: string,
  baseCurrency: "RUB" | "USD",
) {
  const listResponse = await request.get(
    `${apiUrl}/api/v1/portfolios?limit=100&offset=0`,
  );
  expect(listResponse.ok()).toBeTruthy();
  const list = (await listResponse.json()) as { items: TestPortfolio[] };
  const existing = list.items.find((item) => item.name === name);
  if (existing) return existing;

  const createResponse = await request.post(`${apiUrl}/api/v1/portfolios`, {
    data: { name, base_currency: baseCurrency },
  });
  expect(createResponse.ok()).toBeTruthy();
  return (await createResponse.json()) as TestPortfolio;
}

test("synthetic import review survives reload and opens current analytics", async ({
  page,
  request,
}) => {
  const consoleErrors: string[] = [];
  const missingResources: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      const location = message.location();
      consoleErrors.push(
        `${message.text()}${location.url ? ` · ${location.url}:${location.lineNumber}` : ""}`,
      );
    }
  });
  page.on("response", (response) => {
    if (response.status() === 404) missingResources.push(response.url());
  });

  const portfolio = await getOrCreateTestPortfolio(
    request,
    "QA · Import flow",
    "RUB",
  );
  const accountResponse = await request.post(
    `${apiUrl}/api/v1/portfolios/${portfolio.id}/accounts`,
    {
      data: {
        name: `QA import account ${Date.now()}`,
        account_type: "broker",
      },
    },
  );
  expect(accountResponse.ok()).toBeTruthy();
  const account = (await accountResponse.json()) as { id: string };

  await page.goto(`/p/${portfolio.id}/imports/new`);

  await page.getByRole("button", { name: "Т‑Инвестиции" }).click();
  await page.getByLabel("Целевой счёт").selectOption(account.id);
  await page.locator('input[type="file"]').setInputFiles(tbankFixture);
  await page.getByRole("button", { name: "Загрузить и проверить" }).click();
  await page.waitForURL("**/imports/*");
  await page
    .getByRole("heading", { name: "Проверка импорта" })
    .waitFor({ timeout: 45_000 });

  const batchId = page.url().split("/").at(-1);
  expect(batchId).toBeTruthy();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Проверка импорта" }),
  ).toBeVisible();

  const previewResponse = await request.get(
    `${apiUrl}/api/v1/imports/${batchId}/preview?limit=200`,
  );
  expect(previewResponse.ok()).toBeTruthy();
  const preview = (await previewResponse.json()) as {
    items: Array<{ id: string; status: string }>;
  };
  for (const row of preview.items.filter((item) =>
    ["warning", "error", "duplicate"].includes(item.status),
  )) {
    const resolution = await request.patch(
      `${apiUrl}/api/v1/imports/${batchId}/rows/${row.id}`,
      {
        data: {
          action: "exclude",
          note: "Synthetic Playwright review",
        },
      },
    );
    expect(resolution.ok()).toBeTruthy();
  }

  await page.reload();
  await expect(page.getByText("Готово к подтверждению")).toBeVisible();
  await page.getByRole("button", { name: "Подтвердить и пересчитать" }).click();
  await page.waitForURL("**/overview", { timeout: 30_000 });
  await expect(
    page.getByRole("heading", { name: "Обзор портфеля" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Активы", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Активы" })).toBeVisible();
  expect({ consoleErrors, missingResources }).toEqual({
    consoleErrors: [],
    missingResources: [],
  });
});

test("empty overview remains usable at tablet width and from keyboard", async ({
  page,
  request,
}) => {
  const portfolio = await getOrCreateTestPortfolio(
    request,
    "QA · Empty state",
    "RUB",
  );

  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto(`/p/${portfolio.id}/overview`);
  await expect(page.getByText("Портфель пока пуст")).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).not.toHaveJSProperty("tagName", "BODY");
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);

  const completePortfolio = await getOrCreateTestPortfolio(
    request,
    "QA · Complete state",
    "USD",
  );
  const holdingsResponse = await request.get(
    `${apiUrl}/api/v1/portfolios/${completePortfolio.id}/analytics/holdings`,
  );
  expect(holdingsResponse.ok()).toBeTruthy();
  const holdings = (await holdingsResponse.json()) as { items: unknown[] };
  if (!holdings.items.length) {
    const accountResponse = await request.post(
      `${apiUrl}/api/v1/portfolios/${completePortfolio.id}/accounts`,
      { data: { name: "QA complete broker", account_type: "broker" } },
    );
    expect(accountResponse.ok()).toBeTruthy();
    const account = (await accountResponse.json()) as { id: string };
    const operationResponse = await request.post(
      `${apiUrl}/api/v1/operations`,
      {
        data: {
          portfolio_id: completePortfolio.id,
          account_id: account.id,
          occurred_at: "2026-08-20T10:00:00Z",
          time_precision: "second",
          operation_type: "cash_movement",
          payload: {
            direction: "deposit",
            amount: "12345.67",
            currency: "USD",
          },
        },
      },
    );
    expect(operationResponse.ok()).toBeTruthy();
    const recalculateResponse = await request.post(
      `${apiUrl}/api/v1/portfolios/${completePortfolio.id}/positions/recalculate`,
      {
        data: {
          as_of: "2026-08-22T12:00:00Z",
          cost_basis_method: "weighted_average",
        },
      },
    );
    expect(recalculateResponse.ok()).toBeTruthy();
  }
  await page.goto(`/p/${completePortfolio.id}/overview`);
  await expect(page.getByText("Полные данные").first()).toBeVisible();
  await expect(page.getByText("Портфель пока пуст")).toHaveCount(0);
});

test("allocation shows backend actual weights without a target editor", async ({
  page,
  request,
}) => {
  const suffix = Date.now().toString();
  const portfolio = await getOrCreateTestPortfolio(
    request,
    `QA · Actual allocation ${suffix}`,
    "USD",
  );
  const accountResponse = await request.post(
    `${apiUrl}/api/v1/portfolios/${portfolio.id}/accounts`,
    {
      data: {
        name: `QA allocation broker ${suffix}`,
        account_type: "broker",
      },
    },
  );
  expect(accountResponse.ok()).toBeTruthy();
  const account = (await accountResponse.json()) as { id: string };

  const instrumentResponse = await request.post(
    `${apiUrl}/api/v1/instruments`,
    {
      data: {
        name: `QA unpriced equity ${suffix}`,
        instrument_type: "stock",
        currency: "USD",
        identifiers: [
          {
            identifier_type: "provider_code",
            value: `QA-ALLOC-${suffix}`,
            provider: "playwright_synthetic",
          },
        ],
      },
    },
  );
  expect(instrumentResponse.ok()).toBeTruthy();
  const instrument = (await instrumentResponse.json()) as { id: string };

  for (const operation of [
    {
      occurred_at: "2026-08-24T09:00:00Z",
      time_precision: "second",
      operation_type: "cash_movement",
      payload: {
        direction: "deposit",
        amount: "1000",
        currency: "USD",
      },
    },
    {
      occurred_at: "2026-08-24T10:00:00Z",
      time_precision: "second",
      operation_type: "trade",
      payload: {
        side: "buy",
        instrument_id: instrument.id,
        quantity: "1",
        price: "100",
        price_currency: "USD",
      },
    },
  ]) {
    const operationResponse = await request.post(
      `${apiUrl}/api/v1/operations`,
      {
        data: {
          portfolio_id: portfolio.id,
          account_id: account.id,
          ...operation,
        },
      },
    );
    expect(operationResponse.ok()).toBeTruthy();
  }

  const recalculateResponse = await request.post(
    `${apiUrl}/api/v1/portfolios/${portfolio.id}/positions/recalculate`,
    {
      data: {
        as_of: "2026-08-25T00:00:00Z",
        cost_basis_method: "weighted_average",
      },
    },
  );
  expect(recalculateResponse.ok()).toBeTruthy();

  await page.goto(`/p/${portfolio.id}/allocation`);
  await expect(
    page.getByRole("heading", { name: "Распределение", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "USD" }).click();
  await expect(page.getByText("Частично").first()).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Ограничения и качество расчёта" }),
  ).toBeVisible();
  await expect(
    page.getByText("Для одной или нескольких позиций отсутствует цена"),
  ).toBeVisible();
  await expect(page.getByText("market_price_missing")).toHaveCount(0);
  await expect(page.getByText("фактическая доля")).toBeVisible();
  await expect(page.getByText("Целевой план")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Сохранить план" }),
  ).toHaveCount(0);
});
