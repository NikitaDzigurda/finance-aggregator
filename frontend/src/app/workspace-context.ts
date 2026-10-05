import { createContext, useContext } from "react";
import type { Schema } from "../api/client";

export type WorkspaceValue = {
  portfolios: Schema<"PortfolioResponse">[];
  loading: boolean;
  currency: Schema<"ReportingCurrency">;
  setCurrency: (currency: Schema<"ReportingCurrency">) => void;
};

export const WorkspaceContext = createContext<WorkspaceValue | null>(null);

export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("WorkspaceProvider is missing");
  return value;
}
