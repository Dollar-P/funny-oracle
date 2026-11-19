export default function Sparkline({
  data,
  width = 64,
  height = 20,
  color,
  label,
}: {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  label?: string;
}) {
  if (!data || data.length < 2) return <span style={{ width, height }} className="inline-block" aria-hidden="true" />;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const pts = data
    .map((v, i) => `${((i / (data.length - 1)) * width).toFixed(1)},${(height - 2 - ((v - min) / span) * (height - 4)).toFixed(1)}`)
    .join(" ");
  const up = data[data.length - 1] >= data[0];
  const stroke = color ?? (up ? "var(--color-bull)" : "var(--color-bear)");
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <polyline points={pts} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
