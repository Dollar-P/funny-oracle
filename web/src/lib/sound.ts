// Tiny synthesized sounds via Web Audio API — no audio files (REDESIGN §3.3). Off by default.
import { useSettings } from "../state/settings";

let ctx: AudioContext | null = null;
function ac(): AudioContext | null {
  if (!useSettings.getState().sound) return null;
  try {
    ctx = ctx ?? new AudioContext();
    if (ctx.state === "suspended") void ctx.resume();
    return ctx;
  } catch {
    return null;
  }
}

function tone(freq: number, start: number, dur: number, type: OscillatorType = "sine", gain = 0.08) {
  const c = ac();
  if (!c) return;
  const o = c.createOscillator();
  const g = c.createGain();
  o.type = type;
  o.frequency.setValueAtTime(freq, c.currentTime + start);
  g.gain.setValueAtTime(0, c.currentTime + start);
  g.gain.linearRampToValueAtTime(gain, c.currentTime + start + 0.01);
  g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + start + dur);
  o.connect(g).connect(c.destination);
  o.start(c.currentTime + start);
  o.stop(c.currentTime + start + dur + 0.05);
}

function noiseBurst(start: number, dur: number, gain = 0.05) {
  const c = ac();
  if (!c) return;
  const len = Math.floor(c.sampleRate * dur);
  const buf = c.createBuffer(1, len, c.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
  const src = c.createBufferSource();
  src.buffer = buf;
  const f = c.createBiquadFilter();
  f.type = "lowpass";
  f.frequency.value = 900;
  const g = c.createGain();
  g.gain.value = gain;
  src.connect(f).connect(g).connect(c.destination);
  src.start(c.currentTime + start);
}

export const sfx = {
  drumroll(seconds = 1.2) {
    for (let t = 0; t < seconds; t += 0.05) noiseBurst(t, 0.06, 0.03 + (t / seconds) * 0.04);
    noiseBurst(seconds, 0.3, 0.12);
  },
  ding() {
    tone(880, 0, 0.5, "triangle", 0.1);
    tone(1320, 0.08, 0.6, "sine", 0.06);
  },
  buzzer() {
    tone(140, 0, 0.45, "sawtooth", 0.06);
    tone(110, 0.05, 0.45, "square", 0.03);
  },
  tick() {
    tone(1600, 0, 0.04, "square", 0.02);
  },
  fanfare() {
    [523, 659, 784, 1046].forEach((f, i) => tone(f, i * 0.11, 0.35, "triangle", 0.08));
  },
};
