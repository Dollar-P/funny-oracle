import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router-dom";
import TopBar from "./components/TopBar";
import Toasts from "./components/Toasts";
import Arena from "./pages/Arena";
import { DISCLAIMER } from "./lib/cast";

// Duel & Hall of Fame are lazy-loaded (REDESIGN §7 performance)
const Duel = lazy(() => import("./pages/Duel"));
const HallOfFame = lazy(() => import("./pages/HallOfFame"));
const Replay = lazy(() => import("./pages/Replay"));

function Loading() {
  return <div className="py-24 text-center text-ink-dim">🔮 Warming up the stage…</div>;
}

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <div className="stage-bg" aria-hidden="true" />
      <TopBar />
      <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 pb-16">
        <Suspense fallback={<Loading />}>
          <Routes>
            <Route path="/" element={<Arena />} />
            <Route path="/duel" element={<Duel />} />
            <Route path="/hall-of-fame" element={<HallOfFame />} />
            <Route path="/p/:id" element={<Replay />} />
            <Route path="*" element={<div className="py-24 text-center text-ink-dim">Nothing on this stage. 🎭</div>} />
          </Routes>
        </Suspense>
      </main>
      <footer className="text-center text-xs text-ink-faint py-6 px-4">
        🔮 Funny Oracle · {DISCLAIMER}. Prices are model outputs anchored on historical volatility, not promises.
        Charts by{" "}
        <a className="underline hover:text-ink-dim" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
          TradingView Lightweight Charts™
        </a>
        .
      </footer>
      <Toasts />
    </div>
  );
}
