import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, unwrap, type Schema } from "../api/client";
import { WorkspaceContext } from "./workspace-context";

type ReportingCurrency = Schema<"ReportingCurrency">;

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [currency, setCurrency] = useState<ReportingCurrency>("RUB");
  const query = useQuery({
    queryKey: ["portfolios"],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/portfolios", {
          params: { query: { limit: 100 } },
        }),
      ),
  });
  const value = useMemo(
    () => ({
      portfolios: query.data?.items ?? [],
      loading: query.isLoading,
      currency,
      setCurrency,
    }),
    [currency, query.data?.items, query.isLoading],
  );
  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}
