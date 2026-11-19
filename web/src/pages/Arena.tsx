import { CAST, ANALYSTS } from "../lib/cast";

export default function Arena() {
  return (
    <section className="pt-10 text-center">
      <h1 className="font-display text-4xl sm:text-6xl text-gold drop-shadow-[0_0_24px_rgba(250,204,21,0.35)]">
        THE ORACLE ARENA
      </h1>
      <p className="mt-3 text-ink-dim">Five AI analysts. One bull, one bear. The Oracle decides.</p>
      <div className="mt-12 flex flex-wrap justify-center gap-6">
        {ANALYSTS.map((a) => (
          <div key={a} className="flex flex-col items-center gap-2 opacity-60">
            <div
              className="w-20 h-20 rounded-full grid place-items-center text-4xl border-2"
              style={{ borderColor: CAST[a].color, background: "radial-gradient(circle, rgb(255 255 255 / 0.06), transparent 70%)" }}
            >
              {CAST[a].emoji}
            </div>
            <span className="text-sm text-ink-dim">{CAST[a].name}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
