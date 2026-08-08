/* ==========================================================================
   Shared UI kit.
   --------------------------------------------------------------------------
   Every primitive the pages compose from. All styling comes from the component
   classes in styles/components.css, which are built from styles/tokens.css.
   Pages should not define colours, radii or shadows of their own.
   ========================================================================== */

import type { ReactNode } from "react";
import Icon, { type IconName } from "./Icon";
import { toneFor, type Tone } from "../styles/theme";

/* ------------------------------------------------------------------ brand */

export function Brand({
  variant = "full",
  className = "",
}: {
  /** `full` shows the wordmark lockup; `mark` shows just the symbol. */
  variant?: "full" | "mark";
  className?: string;
}) {
  if (variant === "mark") {
    return (
      <img
        src="/brand/mark.png"
        alt="SuwaPath"
        className={`h-9 w-9 object-contain ${className}`}
      />
    );
  }
  return (
    <img
      src="/brand/logo.png"
      alt="SuwaPath — Your Health. Our Path."
      className={`h-9 w-auto object-contain ${className}`}
    />
  );
}

/* ------------------------------------------------------------------ chips */

const CHIP_CLASS: Record<Tone, string> = {
  neutral: "sp-chip-neutral",
  ok: "sp-chip-ok",
  warn: "sp-chip-warn",
  danger: "sp-chip-danger",
  info: "sp-chip-info",
  programme: "sp-chip-programme",
};

export function Chip({
  tone = "neutral",
  icon,
  children,
  className = "",
}: {
  tone?: Tone;
  icon?: IconName;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={`sp-chip ${CHIP_CLASS[tone]} ${className}`}>
      {icon && <Icon name={icon} size={13} />}
      {children}
    </span>
  );
}

const URGENCY_LABEL: Record<string, string> = {
  emergency: "Emergency",
  urgent: "Urgent",
  routine: "Routine",
  self_care: "Self-care",
};

export function UrgencyBadge({ urgency }: { urgency?: string | null }) {
  if (!urgency) return null;
  return (
    <Chip
      tone={toneFor(urgency)}
      icon={urgency === "emergency" ? "emergency" : undefined}
    >
      {URGENCY_LABEL[urgency] ?? urgency.replace(/_/g, " ")}
    </Chip>
  );
}

export function StatusChip({ value }: { value?: string | null }) {
  if (!value) return null;
  return <Chip tone={toneFor(value)}>{value.replace(/_/g, " ")}</Chip>;
}

/* ------------------------------------------------------------------ cards */

export function Card({
  title,
  subtitle,
  action,
  icon,
  children,
  className = "",
  bodyClassName = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  icon?: IconName;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`sp-card ${className}`}>
      {(title || action) && (
        <header className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 p-4 sm:p-5 pb-0 sm:pb-0">
          <div className="flex items-start gap-2.5 min-w-0">
            {icon && (
              <span className="sp-icon-tile bg-brand-50 text-brand-700 !h-8 !w-8">
                <Icon name={icon} size={18} />
              </span>
            )}
            <div className="min-w-0">
              {/* Titles wrap rather than ellipsize: a card header is short on
                  space in a multi-column grid, and a truncated heading reads
                  worse than one that wraps to a second line. */}
              {title && (
                <h2 className="font-semibold text-ink-900 text-base leading-snug">
                  {title}
                </h2>
              )}
              {subtitle && (
                <p className="text-sm text-ink-500 mt-0.5">{subtitle}</p>
              )}
            </div>
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </header>
      )}
      <div className={`p-4 sm:p-5 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

const STAT_TONE: Record<Tone, string> = {
  neutral: "bg-ink-100 text-ink-600",
  ok: "bg-ok-surface text-ok-text",
  warn: "bg-warn-surface text-warn-text",
  danger: "bg-danger-surface text-danger-text",
  info: "bg-brand-50 text-brand-700",
  programme: "bg-programme-surface text-programme-text",
};

export function Stat({
  label,
  value,
  hint,
  icon,
  tone = "info",
  trend,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: IconName;
  tone?: Tone;
  trend?: { direction: "up" | "down"; label: string; good?: boolean };
}) {
  return (
    <div className="sp-card p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-ink-500 leading-snug">{label}</p>
        {icon && (
          <span className={`sp-icon-tile !h-8 !w-8 ${STAT_TONE[tone]}`}>
            <Icon name={icon} size={17} />
          </span>
        )}
      </div>
      {/* Wraps rather than ellipsizing: KPI values are usually a short
          number, but a few (e.g. a specialty name) run long, and an
          unreadable "Endocr…" is worse than a tile that grows a line. */}
      <p
        className="text-xl sm:text-2xl font-bold text-ink-900 mt-1.5 leading-tight break-words"
        title={typeof value === "string" ? value : undefined}
      >
        {value}
      </p>
      {(hint || trend) && (
        <div className="flex items-center gap-1.5 mt-1">
          {trend && (
            <span
              className={`inline-flex items-center gap-0.5 text-xs font-semibold ${
                trend.good === false ? "text-danger-text" : "text-ok-text"
              }`}
            >
              <Icon
                name={trend.direction === "up" ? "trend" : "forecast"}
                size={13}
              />
              {trend.label}
            </span>
          )}
          {hint && <span className="text-xs text-ink-500">{hint}</span>}
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- feedback */

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div
      className="flex items-center justify-center gap-3 py-12 text-ink-500"
      role="status"
    >
      <span className="h-5 w-5 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" />
      <span className="text-sm">{label}</span>
    </div>
  );
}

export function SkeletonCard({ lines = 3 }: { lines?: number }) {
  return (
    <div className="sp-card p-5 space-y-3">
      <div className="sp-skeleton h-4 w-1/3" />
      {Array.from({ length: lines }).map((_, index) => (
        <div key={index} className="sp-skeleton h-3 w-full" />
      ))}
    </div>
  );
}

export function Empty({
  title,
  hint,
  icon = "info",
  action,
}: {
  title: string;
  hint?: string;
  icon?: IconName;
  action?: ReactNode;
}) {
  return (
    <div className="text-center py-10 px-4">
      <span className="sp-icon-tile mx-auto bg-ink-100 text-ink-400 !h-12 !w-12">
        <Icon name={icon} size={24} />
      </span>
      <p className="font-medium text-ink-700 mt-3">{title}</p>
      {hint && <p className="text-sm text-ink-500 mt-1 max-w-sm mx-auto">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Notice({
  tone = "info",
  title,
  children,
  icon,
}: {
  tone?: "info" | "ok" | "warn" | "danger";
  title?: ReactNode;
  children?: ReactNode;
  icon?: IconName;
}) {
  const defaultIcon: Record<string, IconName> = {
    info: "info",
    ok: "circleCheck",
    warn: "warning",
    danger: "error",
  };
  return (
    <div className={`sp-notice sp-notice-${tone}`}>
      <Icon name={icon ?? defaultIcon[tone]} size={20} className="mt-0.5" />
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={title ? "mt-0.5" : ""}>{children}</div>}
      </div>
    </div>
  );
}

export function ErrorNote({ message }: { message?: string | null }) {
  if (!message) return null;
  return <Notice tone="danger">{message}</Notice>;
}

/** Prominent escalation banner for emergency/urgent guidance. */
export function EscalationBanner({
  urgency,
  message,
}: {
  urgency?: string | null;
  message?: string | null;
}) {
  if (!message || !urgency || urgency === "self_care") return null;
  const emergency = urgency === "emergency";
  return (
    <Notice
      tone={emergency ? "danger" : urgency === "urgent" ? "warn" : "info"}
      icon={emergency ? "emergency" : "warning"}
      title={
        emergency ? "Seek urgent medical attention" : "Medical attention advised"
      }
    >
      <p className="text-ink-700">{message}</p>
    </Notice>
  );
}

/* ------------------------------------------------------------ AI elements */

/** Confidence bar — shown next to any AI output (internal rule 10). */
export function Confidence({
  value,
  showLabel = true,
}: {
  value?: number | null;
  showLabel?: boolean;
}) {
  if (value == null) return null;
  const percent = Math.round(value * 100);
  return (
    <div className="flex items-center gap-2">
      {showLabel && <span className="text-xs text-ink-500">Confidence</span>}
      <div className="h-1.5 w-20 rounded-full bg-ink-100 overflow-hidden">
        <div
          className="h-full rounded-full bg-brand-500 transition-all"
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="text-xs font-semibold text-ink-600">{percent}%</span>
    </div>
  );
}

export function AiNotice({ children }: { children: ReactNode }) {
  return (
    <p className="text-xs text-ink-500 flex items-start gap-1.5 mt-3">
      <Icon name="info" size={14} className="mt-px" />
      <span>{children}</span>
    </p>
  );
}

/* ------------------------------------------------------------- structural */

export function PageHeader({
  title,
  subtitle,
  action,
  back,
}: {
  title: string;
  subtitle?: ReactNode;
  action?: ReactNode;
  back?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        {back}
        <h1 className="text-xl sm:text-2xl font-bold text-ink-900">{title}</h1>
        {subtitle && <p className="text-ink-500 mt-0.5 text-sm">{subtitle}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </header>
  );
}

export function Field({
  label,
  value,
}: {
  label: string;
  value?: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <p className="text-xs text-ink-500">{label}</p>
      <p className="text-ink-900 font-medium mt-0.5 break-words">
        {value || <span className="text-ink-400">Not recorded</span>}
      </p>
    </div>
  );
}

/** Responsive table: real table on wide screens, stacked cards on mobile. */
export function DataTable<T>({
  columns,
  rows,
  keyOf,
  onRowClick,
  empty,
}: {
  columns: {
    key: string;
    header: string;
    render: (row: T) => ReactNode;
    /** Hide on small screens where space is tight. */
    hideOnMobile?: boolean;
    /** Used as the card title in the mobile layout. */
    primary?: boolean;
  }[];
  rows: T[];
  keyOf: (row: T) => string;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
}) {
  if (rows.length === 0) return <>{empty}</>;

  return (
    <>
      {/* Desktop / tablet */}
      <div className="sp-table-wrap hidden md:block">
        <table className="sp-table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key}>{column.header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={keyOf(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={onRowClick ? "cursor-pointer" : undefined}
              >
                {columns.map((column) => (
                  <td key={column.key}>{column.render(row)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile: each row becomes a labelled card, so nothing is cut off. */}
      <div className="md:hidden divide-y divide-ink-100">
        {rows.map((row) => {
          const primary = columns.find((c) => c.primary) ?? columns[0];
          const rest = columns.filter(
            (c) => c !== primary && !c.hideOnMobile,
          );
          return (
            <div
              key={keyOf(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={`py-3 ${onRowClick ? "cursor-pointer" : ""}`}
            >
              <div className="font-medium text-ink-900">
                {primary.render(row)}
              </div>
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5">
                {rest.map((column) => (
                  <div key={column.key} className="min-w-0">
                    <dt className="text-xs text-ink-500">{column.header}</dt>
                    <dd className="text-sm text-ink-800 truncate">
                      {column.render(row)}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          );
        })}
      </div>
    </>
  );
}

/* ----------------------------------------------------------- date helpers */

export function formatDateTime(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatTime(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDate(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function relativeDay(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  const today = new Date();
  const days = Math.round(
    (date.setHours(0, 0, 0, 0) - today.setHours(0, 0, 0, 0)) / 86400000,
  );
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days === -1) return "Yesterday";
  return formatDate(value);
}

export function initialsOf(name?: string | null) {
  if (!name) return "?";
  return name
    .replace(/^(Dr\.?|Prof\.?)\s+/i, "")
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export function Avatar({
  name,
  size = 40,
}: {
  name?: string | null;
  size?: number;
}) {
  return (
    <span
      className="sp-avatar"
      style={{ width: size, height: size, fontSize: size * 0.36 }}
    >
      {initialsOf(name)}
    </span>
  );
}

export { Icon };
export type { IconName };
