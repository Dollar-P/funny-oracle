import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { toBlob } from "html-to-image";
import type { Prediction } from "../lib/types";
import ShareCard, { SHARE_H, SHARE_W } from "./ShareCard";
import { useToasts } from "../state/settings";

async function render(node: HTMLElement): Promise<Blob> {
  await document.fonts?.ready;
  const blob = await toBlob(node, { width: SHARE_W, height: SHARE_H, pixelRatio: 1, cacheBust: true });
  if (!blob) throw new Error("could not render the card");
  return blob;
}

export default function ShareButton({ prediction }: { prediction: Prediction }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const push = useToasts((t) => t.push);
  const name = `funny-oracle-${prediction.ticker.replace(/[^A-Z0-9]/gi, "")}-${prediction.mode}-${prediction.horizon}${prediction.id ? `-${prediction.id}` : ""}.png`;

  async function make() {
    if (!ref.current) throw new Error("card not mounted");
    return render(ref.current);
  }
  async function openPreview() {
    setOpen(true);
    setBusy("Rendering…");
    try {
      await new Promise((r) => setTimeout(r, 50));
      const b = await make();
      setPreview(URL.createObjectURL(b));
    } catch (e) {
      push("error", `Share card failed: ${e}`);
    } finally {
      setBusy(null);
    }
  }
  async function download() {
    setBusy("Rendering…");
    try {
      const b = await make();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(b);
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      push("success", "Share card downloaded.");
    } catch (e) {
      push("error", `Download failed: ${e}`);
    } finally {
      setBusy(null);
    }
  }
  async function copy() {
    setBusy("Copying…");
    try {
      const b = await make();
      await navigator.clipboard.write([new ClipboardItem({ "image/png": b })]);
      push("success", "Image copied to the clipboard.");
    } catch (e) {
      push("error", `Copy failed (${e instanceof Error ? e.message : e}) — use Download instead.`);
    } finally {
      setBusy(null);
    }
  }

  const btn = "px-4 h-10 rounded-full text-sm font-semibold inline-flex items-center gap-1.5";
  return (
    <>
      <button onClick={openPreview} className={`${btn} bg-oracle/25 border border-oracle/50 hover:bg-oracle/35`}>
        📸 Share card
      </button>
      {/* offscreen render target */}
      {createPortal(
        <div style={{ position: "fixed", left: -10000, top: 0, pointerEvents: "none" }} aria-hidden="true">
          <ShareCard ref={ref} p={prediction} />
        </div>,
        document.body,
      )}
      {open &&
        createPortal(
          <div className="fixed inset-0 z-50 bg-black/70 grid place-items-center p-4" onClick={() => setOpen(false)}>
            <div
              role="dialog"
              aria-modal="true"
              aria-label="Share card"
              className="card p-4 max-w-[95vw] max-h-[95vh] overflow-auto flex flex-col items-center gap-3"
              onClick={(e) => e.stopPropagation()}
            >
              {preview ? (
                <img src={preview} alt={`Share card for ${prediction.ticker}`} className="max-h-[70vh] w-auto rounded-xl border border-line" />
              ) : (
                <div className="w-[320px] h-[400px] grid place-items-center text-ink-dim">{busy ?? "…"}</div>
              )}
              <div className="flex flex-wrap gap-2 justify-center">
                <button onClick={download} disabled={!!busy} className={`${btn} bg-gold text-stage`} data-testid="share-download">
                  ⬇ Download PNG
                </button>
                <button onClick={copy} disabled={!!busy} className={`${btn} border border-line bg-surface-2`}>
                  📋 Copy image
                </button>
                <button onClick={() => setOpen(false)} className={`${btn} border border-line`} autoFocus>
                  Close
                </button>
              </div>
              <p className="text-xs text-ink-faint">1080 × 1350 PNG · For fun — not investment advice</p>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
