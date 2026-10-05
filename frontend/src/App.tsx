import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useWorkspace } from "./app/workspace-context";
import { Shell } from "./components/Shell";
import { StatePanel } from "./components/ui";

const AllocationPage = lazy(() =>
  import("./pages/AllocationPage").then((module) => ({
    default: module.AllocationPage,
  })),
);
const DataQualityPage = lazy(() =>
  import("./pages/DataQualityPage").then((module) => ({
    default: module.DataQualityPage,
  })),
);
const EventsPage = lazy(() =>
  import("./pages/EventsPage").then((module) => ({
    default: module.EventsPage,
  })),
);
const HoldingsPage = lazy(() =>
  import("./pages/HoldingsPage").then((module) => ({
    default: module.HoldingsPage,
  })),
);
const ImportReviewPage = lazy(() =>
  import("./pages/ImportReviewPage").then((module) => ({
    default: module.ImportReviewPage,
  })),
);
const ImportsPage = lazy(() =>
  import("./pages/ImportsPage").then((module) => ({
    default: module.ImportsPage,
  })),
);
const ImportStartPage = lazy(() =>
  import("./pages/ImportStartPage").then((module) => ({
    default: module.ImportStartPage,
  })),
);
const InstrumentsPage = lazy(() =>
  import("./pages/InstrumentsPage").then((module) => ({
    default: module.InstrumentsPage,
  })),
);
const OnboardingPage = lazy(() =>
  import("./pages/OnboardingPage").then((module) => ({
    default: module.OnboardingPage,
  })),
);
const OverviewPage = lazy(() =>
  import("./pages/OverviewPage").then((module) => ({
    default: module.OverviewPage,
  })),
);

function RootRoute() {
  const { portfolios, loading } = useWorkspace();
  if (loading)
    return (
      <div className="standalone-state">
        <StatePanel title="Проверяем рабочее пространство" kind="loading">
          <p>Запрашиваем список портфелей у серверной части.</p>
        </StatePanel>
      </div>
    );
  if (!portfolios.length) return <Navigate to="/start" replace />;
  return <Navigate to={`/p/${portfolios[0]!.id}/overview`} replace />;
}

export function App() {
  return (
    <Suspense
      fallback={
        <div className="standalone-state">
          <StatePanel title="Открываем экран" kind="loading">
            <p>Загружаем только необходимую часть приложения.</p>
          </StatePanel>
        </div>
      }
    >
      <Routes>
        <Route path="/" element={<RootRoute />} />
        <Route path="/start" element={<OnboardingPage />} />
        <Route path="/p/:portfolioId" element={<Shell />}>
          <Route index element={<Navigate to="overview" replace />} />
          <Route path="overview" element={<OverviewPage />} />
          <Route path="holdings" element={<HoldingsPage />} />
          <Route path="allocation" element={<AllocationPage />} />
          <Route path="events" element={<EventsPage />} />
          <Route path="imports" element={<ImportsPage />} />
          <Route path="imports/new" element={<ImportStartPage />} />
          <Route path="imports/:batchId" element={<ImportReviewPage />} />
          <Route path="instruments" element={<InstrumentsPage />} />
          <Route path="data-quality" element={<DataQualityPage />} />
        </Route>
        <Route path="*" element={<RootRoute />} />
      </Routes>
    </Suspense>
  );
}
