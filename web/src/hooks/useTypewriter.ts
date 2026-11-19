import { useEffect, useState } from "react";
import { prefersReducedMotion } from "../state/settings";

/** Reveal `text` at `cps` characters per second once `active`; `skip()` shows it all. */
export function useTypewriter(text: string, active: boolean, cps = 40, instant = false) {
  const [n, setN] = useState(instant || prefersReducedMotion() ? text.length : 0);
  useEffect(() => {
    if (instant || prefersReducedMotion()) {
      setN(text.length);
      return;
    }
    if (!active || n >= text.length) return;
    const step = Math.max(1, Math.round(cps / 20));
    const t = setInterval(() => setN((x) => Math.min(text.length, x + step)), 1000 / 20);
    return () => clearInterval(t);
  }, [text, active, cps, instant, n >= text.length]); // eslint-disable-line react-hooks/exhaustive-deps
  return { shown: text.slice(0, n), done: n >= text.length, skip: () => setN(text.length) };
}
