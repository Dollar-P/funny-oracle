import { NavLink } from "react-router-dom";
import BudgetMeter from "./BudgetMeter";
import { useSettings } from "../state/settings";
import { DISCLAIMER } from "../lib/cast";
import ModeSwitch from "../pro/ModeSwitch";

const NAV = [
  { to: "/", label: "Arena", icon: "🎪" },
  { to: "/duel", label: "Duel", icon: "⚔️" },
  { to: "/hall-of-fame", label: "Hall of Fame", icon: "🏆" },
];

export default function TopBar() {
  const { sound, toggleSound } = useSettings();
  return (
    <header className="sticky top-0 z-30 backdrop-blur-md bg-stage/70 border-b border-line">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center gap-3 sm:gap-6">
        <NavLink to="/" className="flex items-center gap-2 shrink-0" aria-label="Funny Oracle home">
          <span className="text-2xl" aria-hidden="true">🔮</span>
          <span className="font-display text-xl sm:text-2xl tracking-wide text-gold hidden sm:inline">FUNNY ORACLE</span>
        </NavLink>
        <nav className="flex items-center gap-1" aria-label="Main">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) =>
                `px-2 sm:px-3 py-1.5 rounded-full text-sm font-semibold transition-colors ${
                  isActive ? "bg-surface-2 text-ink shadow-glow" : "text-ink-dim hover:text-ink hover:bg-surface"
                }`
              }
            >
              <span aria-hidden="true">{n.icon}</span>
              <span className="hidden md:inline ml-1.5">{n.label}</span>
              <span className="sr-only md:hidden">{n.label}</span>
            </NavLink>
          ))}
          {/* phones: the Arena ↔ Desk switch collapses into one more icon so the bar never overflows */}
          <NavLink to="/pro" className="sm:hidden px-2 py-1.5 rounded-full text-sm font-semibold text-ink-dim hover:text-ink hover:bg-surface">
            <span aria-hidden="true">📊</span>
            <span className="sr-only">Desk (research mode)</span>
          </NavLink>
        </nav>
        <div className="flex-1" />
        <div className="hidden sm:block">
          <ModeSwitch active="arena" />
        </div>
        <BudgetMeter />
        <button
          onClick={toggleSound}
          className="w-9 h-9 rounded-full border border-line bg-surface hover:bg-surface-2 text-base"
          aria-pressed={sound}
          aria-label={sound ? "Sound on — click to mute" : "Sound off — click to enable"}
          title={sound ? "Sound on" : "Sound off"}
        >
          {sound ? "🔊" : "🔇"}
        </button>
        <span className="hidden lg:inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold border border-gold/40 text-gold bg-gold/10">
          ⚠ {DISCLAIMER}
        </span>
      </div>
      <div className="lg:hidden text-center text-[11px] text-gold/90 bg-gold/5 border-t border-gold/20 py-0.5">
        ⚠ {DISCLAIMER}
      </div>
    </header>
  );
}
