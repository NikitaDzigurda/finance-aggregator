import { chromium, expect } from "@playwright/test";
import { execFileSync, spawn } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { setTimeout as pause } from "node:timers/promises";

// Run only against a freshly migrated, isolated synthetic database.
const api =
  process.env.FINANCE_PHASE05_API_URL ?? "http://127.0.0.1:8012/api/v1";
const front =
  process.env.FINANCE_PHASE05_FRONTEND_URL ?? "http://127.0.0.1:5175";
const root = process.cwd().replace(/\/frontend$/, "");
const testDbName = readFileSync(
  "/private/tmp/phase05-browser-test-db-name",
  "utf8",
).trim();
if (!/^finance_phase05_browser_test_[a-f0-9]{8}$/.test(testDbName))
  throw new Error("A dedicated Phase05 browser test database is required");
process.env.FINANCE_DATABASE_URL = `postgresql+psycopg://finance:finance@127.0.0.1:5432/${testDbName}`;
const importStorage = readFileSync(
  "/private/tmp/phase05-browser-test-storage-root",
  "utf8",
).trim();
if (!importStorage.startsWith("/private/tmp/phase05-import-store-"))
  throw new Error("A dedicated synthetic import storage root is required");
process.env.FINANCE_IMPORT_STORAGE_ROOT = importStorage;
process.env.FINANCE_FX_AUTO_SYNC_ENABLED = "false";
process.env.FINANCE_MARKET_AUTO_SYNC_INTERVAL_SECONDS = "60";

async function post(path, data) {
  const response = await globalThis.fetch(api + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const body = await response.json();
  if (!response.ok)
    throw new Error(`${path} ${response.status} ${JSON.stringify(body)}`);
  return body;
}

async function get(path) {
  const response = await globalThis.fetch(api + path);
  if (!response.ok) throw new Error(`${path} ${response.status}`);
  return response.json();
}

const portfolio = await post("/portfolios", {
  name: "Synthetic Phase05 live browser",
  base_currency: "USD",
});
const broker = await post(`/portfolios/${portfolio.id}/accounts`, {
  name: "Synthetic broker",
  account_type: "broker",
});
const cex = await post(`/portfolios/${portfolio.id}/accounts`, {
  name: "Synthetic Bybit Spot",
  account_type: "cex",
  institution_name: "Bybit",
});

const bundleDirectory = `${root}/docs/fixtures/bybit_complete/csv`;
const bundle = new globalThis.FormData();
for (const [key, value] of Object.entries({
  portfolio_id: portfolio.id,
  account_id: cex.id,
  source_provider: "bybit_spot_csv_bundle",
  declared_format: "csv",
}))
  bundle.append(key, value);
for (const filename of readdirSync(bundleDirectory).filter((file) =>
  file.endsWith(".csv"),
)) {
  bundle.append(
    "file",
    new globalThis.Blob([readFileSync(`${bundleDirectory}/${filename}`)], {
      type: "text/csv",
    }),
    filename,
  );
}
const uploadResponse = await globalThis.fetch(`${api}/imports`, {
  method: "POST",
  body: bundle,
});
if (!uploadResponse.ok)
  throw new Error(
    `Synthetic Bybit upload failed: ${await uploadResponse.text()}`,
  );
const uploaded = await uploadResponse.json();
execFileSync(`${root}/.venv/bin/python`, ["-m", "apps.worker.main", "--once"], {
  cwd: root,
  env: {
    ...process.env,
    PYTHONPATH: `${root}/src`,
    FINANCE_ENVIRONMENT: "test",
    FINANCE_MARKET_AUTO_SYNC_ENABLED: "false",
  },
  timeout: 30_000,
});
const preview = await get(`/imports/${uploaded.batch.id}/preview`);
if (preview.status !== "ready_to_commit" || preview.error_rows !== 0)
  throw new Error(`Synthetic Bybit import not ready: ${preview.status}`);
const confirmed = await post(`/imports/${uploaded.batch.id}/confirm`, {});
if (confirmed.operation_count < 1)
  throw new Error("Synthetic Bybit import did not create Spot operations");
const importedInstruments = (await get("/instruments?limit=100")).items;
const importedAsset = (code) =>
  importedInstruments.find((item) =>
    item.identifiers.some(
      (identifier) =>
        identifier.identifier_type === "crypto_asset_code" &&
        identifier.value === code,
    ),
  );
const qax = importedAsset("QAX");
const qay = importedAsset("QAY");
if (!qax || !qay)
  throw new Error("Synthetic Bybit Spot assets were not imported");

async function instrument(name, type, identifierType, code) {
  const existing = (await get("/instruments?limit=100")).items.find((item) =>
    item.identifiers.some(
      (identifier) =>
        identifier.identifier_type === identifierType &&
        identifier.value === code,
    ),
  );
  if (existing) return existing;
  return post("/instruments", {
    name,
    instrument_type: type,
    currency: "USD",
    identifiers: [{ identifier_type: identifierType, value: code }],
  });
}

const btc = await instrument("BTC", "crypto_asset", "crypto_asset_code", "BTC");
const usdt = await instrument(
  "USDT",
  "crypto_asset",
  "crypto_asset_code",
  "USDT",
);
const stock = await instrument(
  "Synthetic share",
  "stock",
  "isin",
  "XS0000000001",
);
const etf = await instrument("Synthetic fund", "etf", "isin", "XS0000000002");
const artificial = await instrument(
  "Synthetic unsupported coin",
  "crypto_asset",
  "crypto_asset_code",
  "SYNX",
);

async function trade(id, account, quantity, price) {
  return post("/operations", {
    portfolio_id: portfolio.id,
    account_id: account.id,
    occurred_at: "2026-09-01T09:00:00Z",
    time_precision: "second",
    operation_type: "trade",
    payload: {
      side: "buy",
      instrument_id: id,
      quantity,
      price,
      price_currency: "USD",
    },
  });
}

await trade(btc.id, cex, "2", "10");
await trade(stock.id, broker, "3", "8");
await trade(etf.id, broker, "4", "15");
await trade(artificial.id, cex, "1", "2");
await post("/operations", {
  portfolio_id: portfolio.id,
  account_id: cex.id,
  occurred_at: "2026-09-02T09:00:00Z",
  time_precision: "second",
  operation_type: "balance_adjustment",
  payload: {
    instrument_id: usdt.id,
    quantity_change: "5",
    reason: "Synthetic unknown acquisition basis",
  },
});
for (const [id, price] of [
  [btc.id, "12"],
  [stock.id, "11"],
  [etf.id, "20"],
]) {
  await post("/prices", {
    instrument_id: id,
    price,
    currency: "USD",
    observed_at: "2026-09-14T09:00:00Z",
  });
}
await post(`/portfolios/${portfolio.id}/positions/recalculate`, {
  as_of: new Date().toISOString(),
});
const before = await get(
  `/portfolios/${portfolio.id}/analytics/holdings?reporting_currency=USD`,
);
const beforeKnown = before.current_value.known_value;

let worker;
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
try {
  await page.goto(`${front}/p/${portfolio.id}/overview`);
  await page.waitForLoadState("networkidle");
  await page.locator(".currency-switch button").last().click();
  await page.waitForLoadState("networkidle");
  await expect(
    page.getByText("Текущая стоимость", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Powered by Coinparika")).toBeVisible();
  await page.getByRole("link", { name: "Активы", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Активы" })).toBeVisible();
  for (const name of [
    "Synthetic share",
    "Synthetic fund",
    "Synthetic unsupported coin",
  ])
    await expect(page.getByText(name)).toBeVisible();
  for (const name of ["QAX", "QAY"])
    await expect(
      page.locator(".vrow").filter({ hasText: name }).first(),
    ).toBeVisible();
  const initialVisibleValue = await page
    .locator(".holdings-summary strong")
    .first()
    .innerText();

  worker = spawn(`${root}/.venv/bin/python`, ["-m", "apps.worker.main"], {
    cwd: root,
    env: {
      ...process.env,
      PYTHONPATH: `${root}/src`,
      FINANCE_ENVIRONMENT: "development",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const workerOutput = [];
  worker.stdout.on("data", (data) => workerOutput.push(data.toString()));
  worker.stderr.on("data", (data) => workerOutput.push(data.toString()));

  let after;
  for (let attempt = 0; attempt < 25; attempt++) {
    await pause(1500);
    after = await get(
      `/portfolios/${portfolio.id}/analytics/holdings?reporting_currency=USD`,
    );
    if (after.current_value.known_value !== beforeKnown) break;
  }
  if (!after || after.current_value.known_value === beforeKnown)
    throw new Error(
      `Background valuation did not change: ${workerOutput.join("").slice(-1200)}`,
    );
  await expect(page.locator(".holdings-summary strong").first()).not.toHaveText(
    initialVisibleValue,
    { timeout: 40_000 },
  );
  const holdings = after.items.filter((item) => item.kind === "instrument");
  const usdtHolding = holdings.find((item) => item.instrument_id === usdt.id);
  const unsupportedHolding = holdings.find(
    (item) => item.instrument_id === artificial.id,
  );
  const importedHoldings = [qax.id, qay.id].map((id) =>
    holdings.find((item) => item.instrument_id === id),
  );
  if (
    !usdtHolding ||
    usdtHolding.market_value === null ||
    usdtHolding.cost_basis !== null
  )
    throw new Error("USDT unknown basis changed after live valuation");
  if (!unsupportedHolding || unsupportedHolding.market_value !== null)
    throw new Error("Unsupported synthetic asset received a price");
  if (importedHoldings.some((item) => !item || item.market_value !== null))
    throw new Error("Synthetic Bybit assets should remain visibly unpriced");
  const latestJob = await get("/market-data/sync/latest");
  if (
    latestJob?.status !== "succeeded" ||
    latestJob.counters?.inserted < 1 ||
    latestJob.counters?.failed_portfolios !== 0
  )
    throw new Error(
      "Safe market sync diagnostics did not report the successful background job",
    );
  const btcRow = page.locator(".vrow").filter({ hasText: "BTC" }).first();
  await btcRow.locator("details.price-detail summary").click();
  await expect(
    btcRow.getByText("глобальная агрегированная оценка"),
  ).toBeVisible();
  await page.screenshot({
    path: "/private/tmp/phase05-live-browser.png",
    fullPage: true,
  });
  if (consoleErrors.length)
    throw new Error(`Browser console errors: ${consoleErrors.join("; ")}`);
  globalThis.console.log(
    JSON.stringify({
      beforeKnown,
      afterKnown: after.current_value.known_value,
      sourceDetailsVisible: true,
      unknownBasisPreserved: true,
      unsupportedUnpriced: true,
      safeJobDiagnostics: latestJob.counters,
      bybitSpotImportedOperations: confirmed.operation_count,
      bybitSpotPositionsVisible: true,
      consoleErrors,
      workerLog: workerOutput.join("").slice(-650),
    }),
  );
} finally {
  if (worker) {
    worker.kill("SIGTERM");
    await pause(500);
  }
  await browser.close();
}
