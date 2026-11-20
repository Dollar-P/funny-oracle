import { Suspense, lazy } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { pro } from "./api";
import { Badge, tzName, useDeskEvents } from "./ui";
import ModeSwitch from "./ModeSwitch";

const Overview = lazy(() => import("./pages/Overview"));
const Signals = lazy(() => import("./pages/Signals"));
const SignalDetail = lazy(() => import("./pages/SignalDetail"));
const Positions = lazy(() => import("./pages/Positions"));
const Journal = lazy(() => import("./pages/Journal"));
const Lab = lazy(() => import("./pages/Lab"));
const Risk = lazy(() => import("./pages/Risk"));
const SettingsPage = lazy(() => import("./pages/Settings"));

const NAV = [
  { to: "/pro", label: "Overview", end: true },
  { to: "/pro/signals", label: "Signals" },
  { to: "/pro/positions", label: "Positions" },
  { to: "/pro/journal", label: "Journal" },
  { to: "/pro/lab", label: "Lab" },
  { to: "/pro/risk", label: "Risk" },
  { to: "/pro/settings", label: "Settings" },
];

export default function DeskApp() {
  const flash = useDeskEvents();
  const ov = useQuery({ queryKey: ["overview"], queryFn: pro.overview, refetchInterval: 30_000 });
  const pending = ov.data?.signal_counts?.new ?? 0;
  return (
    <div className="desk font-sans text-[13px]">
      <header className="sticky top-0 z-30 border-b border-desk-line bg-desk-bg/95 backdrop-blur">
        <div className="mx-auto flex h-12 max-w-[1400px] items-center gap-3 px-4">
          <span className="shrink-0 text-[14px] font-semibold tracking-wide">
            Funny Oracle <span className="text-desk-faint">·</span> <span className="text-desk-accent">Desk</span>
          </span>
          <ModeSwitch active="desk" />
          <div className="flex-1" />
          {ov.data?.halted && <Badge tone="down" title="Max-drawdown breaker tripped; reset the account in Settings">halted</Badge>}
          <Badge tone="warn">paper only</Badge>
          <span className="hidden text-[11px] text-desk-faint md:inline">
            {tzName()} · as of {ov.data ? new Date(ov.data.as_of).toLocaleString("en-GB", { hour12: false }) : "…"}
          </span>
        </div>
        <nav className="mx-auto flex max-w-[1400px] gap-1 overflow-x-auto px-3 pb-1.5" aria-label="Desk">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => `shrink-0 rounded px-2.5 py-1 text-[12.5px] ${isActive ? "bg-desk-panel-2 text-desk-ink" : "text-desk-dim hover:text-desk-ink"}`}>
              {n.label}
              {n.label === "Signals" && pending > 0 && <span className="ml-1.5 rounded bg-desk-accent/20 px-1 text-[11px] text-desk-accent">{pending}</span>}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="mx-auto max-w-[1400px] px-4 pb-12 pt-4">
        <Suspense fallback={<div className="py-16 text-center text-desk-faint">Loading…</div>}>
          <Routes>
            <Route index element={<Overview />} />
            <Route path="signals" element={<Signals flash={flash} />} />
            <Route path="signals/:id" element={<SignalDetail />} />
            <Route path="positions" element={<Positions />} />
            <Route path="journal" element={<Journal />} />
            <Route path="lab" element={<Lab />} />
            <Route path="risk" element={<Risk />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<div className="py-16 text-center text-desk-faint">Unknown Desk page.</div>} />
          </Routes>
        </Suspense>
      </main>
      <footer className="border-t border-desk-line px-4 py-4 text-center text-[11px] text-desk-faint">
        Research tool. Paper trading only — no orders reach any broker. Not investment advice. Small samples are labelled
        "insufficient data".
      </footer>
    </div>
  );
}
