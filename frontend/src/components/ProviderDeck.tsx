/**
 * A fanned deck of provider cards the patient can flick through and pick.
 *
 * Suggestions arrive as a set — five dermatologists, four places that do an
 * MRI — and a vertical list of five cards buries the conversation. A deck
 * shows one card in focus with the rest fanned behind it, so the set is
 * visibly a set without costing five cards of height.
 *
 * The cards are built from the structured payload the tools return, never
 * from the model's prose. A doctor's name, fee and next free slot come from
 * the database row; the model only decides which ones to surface. That
 * matters here more than in most UI: a hallucinated appointment time that
 * looks like a real bookable card is a much worse failure than a hallucinated
 * sentence.
 *
 * Keyboard and pointer both work — arrow keys move the deck, and every card
 * is reachable by tab. A carousel that only responds to a drag is unusable
 * for anyone who does not use a mouse.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Icon, type IconName } from "./ui";

export interface ProviderCard {
  kind: "doctor" | "hospital" | "test";
  name: string;
  // Doctor
  doctor_id?: string;
  specialty?: string;
  sub_specialty?: string | null;
  hospital_name?: string | null;
  languages?: string[];
  fee_lkr?: number;
  rating?: number;
  years_experience?: number;
  teleconsultation?: boolean;
  next_available?: { label: string; date_label: string; start: string } | null;
  distance_km?: number | null;
  // Hospital
  hospital_id?: string;
  city?: string;
  facility_type?: string;
  has_emergency?: boolean;
  is_24_hours?: boolean;
  capabilities?: string[];
  // Test
  test_id?: string;
  category?: string;
  min_price_lkr?: number;
  max_price_lkr?: number;
  offerings?: {
    hospital_name: string;
    city: string;
    price_lkr: number;
    turnaround_hours: number;
  }[];
}

interface Props {
  providers: ProviderCard[];
  onSelect?: (provider: ProviderCard) => void;
  title?: string;
}

const KIND_META: Record<string, { icon: IconName; label: string }> = {
  doctor: { icon: "stethoscope", label: "Doctor" },
  hospital: { icon: "hospital", label: "Facility" },
  test: { icon: "lab", label: "Test" },
};

const money = (value?: number) =>
  value == null ? null : `LKR ${Math.round(value).toLocaleString("en-LK")}`;

export default function ProviderDeck({ providers, onSelect, title }: Props) {
  const [active, setActive] = useState(0);
  const [narrow, setNarrow] = useState(false);
  const deckRef = useRef<HTMLDivElement>(null);
  const touchStart = useRef<number | null>(null);

  // On a phone the fanned cards spill off both edges, so the deck collapses
  // to a single card with the neighbours only just peeking. Measured from the
  // container rather than a viewport breakpoint, because the same component
  // renders inside a chat bubble whose width is not the viewport's.
  useEffect(() => {
    const element = deckRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) =>
      setNarrow(entry.contentRect.width < 380),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const count = providers.length;
  const clamp = useCallback(
    (index: number) => Math.max(0, Math.min(count - 1, index)),
    [count],
  );

  useEffect(() => {
    setActive((current) => clamp(current));
  }, [clamp]);

  if (count === 0) return null;

  const go = (delta: number) => setActive((current) => clamp(current + delta));

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      go(1);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      go(-1);
    }
  }

  return (
    <div className="rounded-2xl border border-line bg-surface p-3 sm:p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-400">
          {title ?? `${count} suggestion${count > 1 ? "s" : ""}`}
        </p>
        <span className="text-xs text-ink-400">
          {active + 1} / {count}
        </span>
      </div>

      <div
        ref={deckRef}
        tabIndex={0}
        role="group"
        aria-label={title ?? "Suggestions"}
        onKeyDown={onKeyDown}
        onTouchStart={(event) => {
          touchStart.current = event.touches[0].clientX;
        }}
        onTouchEnd={(event) => {
          if (touchStart.current === null) return;
          const delta = event.changedTouches[0].clientX - touchStart.current;
          if (Math.abs(delta) > 40) go(delta < 0 ? 1 : -1);
          touchStart.current = null;
        }}
        className="relative h-[15.5rem] touch-pan-y rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
      >
        {providers.map((provider, index) => {
          const offset = index - active;
          const distance = Math.abs(offset);
          // Cards more than two away are parked behind the deck rather than
          // unmounted, so flicking through does not remount and refetch.
          const hidden = distance > 2;
          const spread = narrow ? 22 : 46;
          const tilt = narrow ? 2 : 4;

          return (
            <button
              key={`${provider.kind}-${provider.doctor_id ?? provider.hospital_id ?? provider.test_id ?? index}`}
              type="button"
              aria-hidden={hidden}
              tabIndex={offset === 0 ? 0 : -1}
              onClick={() => (offset === 0 ? onSelect?.(provider) : setActive(index))}
              className="absolute left-1/2 top-0 w-[min(19rem,88%)] text-left transition-all duration-300 ease-out"
              style={{
                transform: `translateX(-50%) translateX(${offset * spread}px) rotate(${offset * tilt}deg) scale(${offset === 0 ? 1 : 0.9 - distance * 0.03})`,
                zIndex: 20 - distance,
                opacity: hidden ? 0 : offset === 0 ? 1 : 0.55,
                pointerEvents: hidden ? "none" : "auto",
              }}
            >
              <Card provider={provider} focused={offset === 0} />
            </button>
          );
        })}
      </div>

      <div className="mt-2 flex items-center justify-center gap-3">
        <button
          type="button"
          onClick={() => go(-1)}
          disabled={active === 0}
          aria-label="Previous suggestion"
          className="grid h-8 w-8 place-items-center rounded-full border border-line bg-surface text-ink-600 transition hover:border-brand-400 hover:text-brand-700 disabled:opacity-35"
        >
          <Icon name="chevronLeft" size={16} />
        </button>

        <div className="flex items-center gap-1.5">
          {providers.map((_, index) => (
            <button
              key={index}
              type="button"
              onClick={() => setActive(index)}
              aria-label={`Suggestion ${index + 1}`}
              aria-current={index === active}
              className={`h-1.5 rounded-full transition-all ${
                index === active ? "w-5 bg-brand-600" : "w-1.5 bg-ink-200"
              }`}
            />
          ))}
        </div>

        <button
          type="button"
          onClick={() => go(1)}
          disabled={active === count - 1}
          aria-label="Next suggestion"
          className="grid h-8 w-8 place-items-center rounded-full border border-line bg-surface text-ink-600 transition hover:border-brand-400 hover:text-brand-700 disabled:opacity-35"
        >
          <Icon name="chevronRight" size={16} />
        </button>
      </div>
    </div>
  );
}

function Card({
  provider,
  focused,
}: {
  provider: ProviderCard;
  focused: boolean;
}) {
  const meta = KIND_META[provider.kind] ?? KIND_META.doctor;

  return (
    <div
      className={`h-[15rem] overflow-hidden rounded-xl border bg-surface p-3.5 ${
        focused
          ? "border-brand-300 shadow-lg shadow-brand-600/10"
          : "border-line shadow-sm"
      }`}
    >
      <div className="flex items-start gap-2.5">
        <span className="sp-icon-tile shrink-0 bg-brand-50 text-brand-700">
          <Icon name={meta.icon} size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium uppercase tracking-wide text-ink-400">
            {meta.label}
          </p>
          <p className="font-semibold leading-snug text-ink-900 break-words">
            {provider.name}
          </p>
        </div>
        {provider.rating != null && (
          <span className="flex shrink-0 items-center gap-0.5 text-xs text-ink-500">
            <Icon name="star" size={13} className="text-warn-text" />
            {provider.rating.toFixed(1)}
          </span>
        )}
      </div>

      <div className="mt-2.5 space-y-1.5 text-sm text-ink-600">
        {provider.kind === "doctor" && <DoctorBody provider={provider} />}
        {provider.kind === "hospital" && <HospitalBody provider={provider} />}
        {provider.kind === "test" && <TestBody provider={provider} />}
      </div>

      {focused && (
        <p className="mt-2.5 flex items-center gap-1 text-xs font-medium text-brand-700">
          Tap to continue
          <Icon name="arrowRight" size={13} />
        </p>
      )}
    </div>
  );
}

function Row({ icon, children }: { icon: IconName; children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5">
      <Icon name={icon} size={14} className="mt-0.5 shrink-0 text-ink-400" />
      <span className="min-w-0 break-words">{children}</span>
    </p>
  );
}

function DoctorBody({ provider }: { provider: ProviderCard }) {
  return (
    <>
      <Row icon="stethoscope">
        {provider.specialty}
        {provider.sub_specialty ? ` · ${provider.sub_specialty}` : ""}
      </Row>
      {provider.hospital_name && (
        <Row icon="hospital">
          {provider.hospital_name}
          {provider.distance_km != null ? ` · ${provider.distance_km} km` : ""}
        </Row>
      )}
      {provider.languages && provider.languages.length > 0 && (
        <Row icon="language">{provider.languages.join(", ")}</Row>
      )}
      {provider.fee_lkr != null && <Row icon="payments">{money(provider.fee_lkr)}</Row>}
      {provider.next_available && (
        <p className="pt-0.5 text-xs font-semibold text-brand-700">
          Next: {provider.next_available.date_label}, {provider.next_available.label}
        </p>
      )}
    </>
  );
}

function HospitalBody({ provider }: { provider: ProviderCard }) {
  return (
    <>
      <Row icon="location">
        {provider.city}
        {provider.facility_type
          ? ` · ${provider.facility_type.replace(/_/g, " ")}`
          : ""}
      </Row>
      <Row icon="clock">
        {provider.is_24_hours ? "Open 24 hours" : "Check opening hours"}
      </Row>
      {provider.has_emergency && (
        <p className="inline-flex items-center gap-1 rounded-full bg-danger-surface px-2 py-0.5 text-xs font-medium text-danger-text">
          <Icon name="emergency" size={12} />
          Emergency department
        </p>
      )}
      {provider.capabilities && provider.capabilities.length > 0 && (
        <Row icon="lab">{provider.capabilities.slice(0, 3).join(", ")}</Row>
      )}
    </>
  );
}

function TestBody({ provider }: { provider: ProviderCard }) {
  const cheapest = provider.offerings?.[0];
  return (
    <>
      {provider.category && <Row icon="lab">{provider.category}</Row>}
      {provider.min_price_lkr != null && (
        <Row icon="payments">
          {provider.min_price_lkr === provider.max_price_lkr
            ? money(provider.min_price_lkr)
            : `${money(provider.min_price_lkr)} – ${money(provider.max_price_lkr)}`}
        </Row>
      )}
      {cheapest && (
        <Row icon="hospital">
          Cheapest: {cheapest.hospital_name}, {cheapest.city}
        </Row>
      )}
      {cheapest && (
        <p className="pt-0.5 text-xs font-semibold text-brand-700">
          Results in about {cheapest.turnaround_hours} hours
        </p>
      )}
    </>
  );
}
