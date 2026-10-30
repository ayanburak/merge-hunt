/**
 * file: src/format.ts
 * importable: number, day, clock, duration, ago
 * description: How numbers, dates and times are written on the screen.
 */

// The dates come from the backend in UTC with their offset, the Date object turns them into the
// user's own time zone by itself.

const NUMBER = new Intl.NumberFormat("en-US");
const DAY = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });
const CLOCK = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });

export function number(value: number): string {
  return NUMBER.format(value);
}

export function day(iso: string): string {
  return DAY.format(new Date(iso));
}

export function clock(iso: string): string {
  return CLOCK.format(new Date(iso));
}

// Written as 12d ago, in months once days stop being useful and in years after two of them.
export function ago(iso: string): string {
  const days = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 86400000));
  if (days === 0) return "today";
  if (days < 60) return `${days}d ago`;
  if (days >= 730) return `${Math.round(days / 365)}y ago`;
  return `${Math.round(days / 30)}mo ago`;
}

// Written as 14m 27s, an hour long scan as 1h 05m.
export function duration(seconds: number): string {
  const whole = Math.round(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor(whole / 60) % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  if (hours > 0) return `${hours}h ${pad(minutes)}m`;
  return `${minutes}m ${pad(whole % 60)}s`;
}
