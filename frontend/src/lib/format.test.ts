import { describe, expect, it } from "vitest";
import {
  diagnosticCountLabel,
  formatMoney,
  historicalConversionMessage,
  impactLabel,
  userFacingName,
} from "./format";

describe("financial display states", () => {
  it("does not render null as zero", () => {
    expect(formatMoney(null, "RUB")).toBe("Недоступно");
    expect(formatMoney("0", "RUB")).toBe("0,00 ₽");
    expect(formatMoney(null, "RUB")).not.toBe(formatMoney("0", "RUB"));
  });

  it("formats a long decimal without converting it to binary number", () => {
    expect(formatMoney("12345678901234567890.125", "USD")).toBe(
      "12 345 678 901 234 567 890,13 $",
    );
  });

  it("explains diagnostic counters with Russian plural forms", () => {
    expect(diagnosticCountLabel("cost_basis_unknown", 1)).toBe("1 позиция");
    expect(diagnosticCountLabel("cost_basis_unknown", 6)).toBe("6 позиций");
    expect(diagnosticCountLabel("import_history_period_limited", 4)).toBe(
      "4 импорта",
    );
  });

  it("translates data-quality impacts for the primary interface", () => {
    expect(impactLabel("cost_basis", "partial")).toBe(
      "Себестоимость: рассчитана частично",
    );
    expect(impactLabel("unrealised_pnl", "unavailable")).toBe(
      "Нереализованный результат: недоступен",
    );
    expect(impactLabel("cost_basis", "unavailable", "analytics/holdings")).toBe(
      "Себестоимость затронутых позиций: недоступна",
    );
  });

  it("uses the approved historical conversion explanation", () => {
    expect(historicalConversionMessage).toBe(
      "События показаны в исходных валютах. Для пересчёта на дату операции не хватает исторических курсов.",
    );
  });

  it("localizes known synthetic names without changing stored data", () => {
    expect(userFacingName("Swagger broker account updated")).toBe(
      "Проверочный брокерский счёт",
    );
    expect(userFacingName("Личный счёт")).toBe("Личный счёт");
  });
});
