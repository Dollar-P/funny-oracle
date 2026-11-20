import { NavLink } from "react-router-dom";

/** Top-level mode switch "Arena ↔ Desk" (PRO.md §0, §7). Shared by both top bars. */
export default function ModeSwitch({ active }: { active: "arena" | "desk" }) {
  const base = "px-2.5 py-1 text-[12px] font-semibold transition-colors";
  const on = active === "desk" ? "bg-[#1b1f26] text-[#e4e7eb]" : "bg-surface-2 text-ink";
  const off = active === "desk" ? "text-[#9aa3ae] hover:text-[#e4e7eb]" : "text-ink-dim hover:text-ink";
  return (
    <div role="group" aria-label="Mode" className={`inline-flex shrink-0 overflow-hidden rounded-md border ${active === "desk" ? "border-[#262b33]" : "border-line"}`}>
      <NavLink to="/" end className={`${base} ${active === "arena" ? on : off}`} aria-current={active === "arena" ? "page" : undefined}>
        Arena
      </NavLink>
      <NavLink to="/pro" className={`${base} ${active === "desk" ? on : off}`} aria-current={active === "desk" ? "page" : undefined}>
        Desk
      </NavLink>
    </div>
  );
}
