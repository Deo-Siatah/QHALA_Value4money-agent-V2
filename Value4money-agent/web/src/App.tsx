import { Route, Routes } from "react-router-dom";
import { Header } from "./components/Header";
import { RunPage } from "./pages/RunPage";
import { RunsPage } from "./pages/RunsPage";
import { StartPage } from "./pages/StartPage";

export function App() {
  return (
    <div className="min-h-screen">
      <Header />
      <main>
        <Routes>
          <Route path="/" element={<StartPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="*" element={<StartPage />} />
        </Routes>
      </main>
    </div>
  );
}
