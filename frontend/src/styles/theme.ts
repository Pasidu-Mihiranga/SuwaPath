/**
 * JavaScript mirror of the design tokens.
 *
 * Some libraries (Recharts in particular) need real colour values rather than
 * CSS custom properties, because they write colours into SVG attributes. Rather
 * than hard-coding hexes at each call site, they are read once from the
 * computed styles of :root — so tokens.css remains the single source of truth
 * and a token change flows through to charts automatically.
 */

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return value || fallback;
}

/** Resolved lazily: :root must be styled before these are read. */
export const chartColors = {
  get brand() {
    return cssVar("--sp-brand-600", "#0090b0");
  },
  get brandLight() {
    return cssVar("--sp-brand-300", "#63e0f2");
  },
  get ink() {
    return cssVar("--sp-ink-500", "#4a75a3");
  },
  get grid() {
    return cssVar("--sp-border", "#e6ecf4");
  },
  get muted() {
    return cssVar("--sp-ink-200", "#c7d7ea");
  },
  get danger() {
    return cssVar("--sp-danger-text", "#912018");
  },
  get dangerSolid() {
    return cssVar("--sp-danger-solid", "#dc2626");
  },
  get warn() {
    return cssVar("--sp-warn-solid", "#ea580c");
  },
  get ok() {
    return cssVar("--sp-ok-solid", "#16a34a");
  },
  get programme() {
    return cssVar("--sp-programme-solid", "#7c5cff");
  },
};

/** Shared Recharts axis/tick styling so every chart matches. */
export const chartAxis = {
  tick: { fontSize: 11, fill: chartColors.ink },
  axisLine: false as const,
  tickLine: false as const,
};

/** Tailwind breakpoints, for the rare case JS needs to branch on viewport. */
export const breakpoints = {
  sm: 640,
  md: 768,
  lg: 1024,
  xl: 1280,
} as const;

/** Semantic tone names used by chips, notices and status pills. */
export type Tone =
  | "neutral"
  | "ok"
  | "warn"
  | "danger"
  | "info"
  | "programme";

/** Maps a backend status/urgency string to a visual tone, in one place. */
export const TONE_BY_STATUS: Record<string, Tone> = {
  // urgency
  emergency: "danger",
  urgent: "warn",
  routine: "info",
  self_care: "ok",
  // appointment lifecycle
  completed: "ok",
  confirmed: "info",
  pending: "warn",
  available: "neutral",
  checked_in: "info",
  in_consultation: "programme",
  cancelled: "neutral",
  no_show: "danger",
  // risk bands
  high: "danger",
  medium: "warn",
  low: "ok",
  // lab result flags
  normal: "ok",
  critical: "danger",
  unknown: "neutral",
  // alert severity
  attention: "warn",
  info: "info",
  // verification
  verified: "ok",
  rejected: "danger",
  // processing
  processing: "info",
  failed: "danger",
};

export function toneFor(status?: string | null): Tone {
  if (!status) return "neutral";
  return TONE_BY_STATUS[status] ?? "neutral";
}
