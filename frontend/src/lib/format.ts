const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

/** ₹ crore figure -> "₹78,750 Cr" or "₹1.29 L Cr" for very large numbers. */
export function cr(v: number | null | undefined, opts: { short?: boolean } = {}): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v < 0 ? "−" : "";
  const a = Math.abs(v);
  if (opts.short && a >= 100000) return `${sign}₹${(a / 100000).toFixed(2)} L Cr`;
  if (a < 100) return `${sign}₹${a.toFixed(1)} Cr`;
  return `${sign}₹${inr.format(a)} Cr`;
}

export function price(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `₹${v >= 1000 ? inr.format(v) : inr2.format(v)}`;
}

export function pct(v: number | null | undefined, digits = 1, plus = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (Math.abs(v) >= 1000) return `${v > 0 && plus ? "+" : ""}${(v / 100).toFixed(0)}×`;
  const s = v.toFixed(Math.abs(v) >= 100 ? 0 : digits);
  return `${v > 0 && plus ? "+" : v < 0 ? "−" : ""}${s.replace("-", "")}%`;
}

export function tone(v: number | null | undefined): string {
  if (v === null || v === undefined || Math.abs(v) < 0.05) return "text-mute";
  return v > 0 ? "text-up" : "text-down";
}

export function ago(iso: string, now = Date.now()): string {
  const t = new Date(iso).getTime();
  const s = Math.max(0, (now - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  const d = Math.floor(s / 86400);
  return d === 1 ? "yesterday" : `${d}d ago`;
}

export function dayLabel(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
}

export function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
}

export function compact(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return inr.format(v);
}

export function cls(...xs: (string | false | null | undefined)[]): string {
  return xs.filter(Boolean).join(" ");
}

export function haptic(ms = 8) {
  try { navigator.vibrate?.(ms); } catch { /* not supported */ }
}
