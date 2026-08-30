/**
 * The hands-free emergency path, mounted once for the whole app.
 *
 * Everything else in SuwaPath needs a patient who can hold a phone and tap a
 * button. This is for the minute when they cannot: a spoken sentence is
 * screened by the deterministic rule engine, and if it matches an emergency
 * the nearest emergency departments and any consenting guardian are alerted,
 * an avatar reads the escalation and the first-aid steps aloud, and the
 * ambulance number is one tap away. Nobody has to look at the screen for any
 * of that to happen.
 *
 * Two things are shown that a slicker version would hide, because hiding them
 * would make this untrustworthy:
 *
 * **What it heard.** Speech recognition mishears, and Sinhala and Tamil
 * recognition mishears more. A patient looking at an emergency overlay they
 * did not ask for is owed the sentence that caused it — that is the difference
 * between a false alarm they can dismiss and an app that has started behaving
 * strangely.
 *
 * **That the microphone is on.** An always-on microphone that does not say so
 * continuously is a surveillance device. The indicator is not decoration; it
 * is the consent staying visible for as long as the listening does.
 */

import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import DoctorAvatar from "../Avatar/DoctorAvatar";
import Icon from "../Icon";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import {
  useEmergencyVoice,
  type EmergencyAlert,
  type EmergencyFacility,
} from "./useEmergencyVoice";

interface Readiness {
  ambulance_number: string;
  ambulance_name: string;
  guardians_who_would_be_told: { name: string; relationship: string }[];
  facilities_alerted_per_event: number;
  cooldown_minutes: number;
  has_location_on_file: boolean;
}

/* ------------------------------------------------------------------ */
/* Overlay                                                            */
/* ------------------------------------------------------------------ */

function FacilityRow({ facility }: { facility: EmergencyFacility }) {
  return (
    <li className="flex items-start justify-between gap-3 border-t border-line py-2.5 first:border-t-0">
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-ink-900">{facility.name}</p>
        <p className="mt-0.5 text-xs text-ink-600">
          {facility.distance_km} km away
          {facility.city ? ` · ${facility.city}` : ""}
          {facility.is_24_hours ? " · open 24 hours" : ""}
        </p>
      </div>
      {facility.phone ? (
        <a
          href={`tel:${facility.phone}`}
          className="sp-btn sp-btn-ghost shrink-0 !py-1.5 text-xs"
        >
          <Icon name="phone" size={14} />
          Call
        </a>
      ) : null}
    </li>
  );
}

function EmergencyOverlay({
  alert,
  language,
  onDismiss,
}: {
  alert: EmergencyAlert;
  language: string;
  onDismiss: () => void;
}) {
  // Nothing here is escapable by keyboard on purpose — no Escape handler and
  // no click-outside. An overlay that vanishes because a frightened person
  // fumbled the phone has failed at its only job. Dismissing is a labelled
  // button that says what it means.
  return (
    <div
      className="fixed inset-0 z-[90] overflow-y-auto bg-ink-900/70 p-3 sm:p-6"
      role="alertdialog"
      aria-modal="true"
      aria-label="Emergency detected"
    >
      <div className="mx-auto w-full max-w-lg rounded-3xl border border-danger-border bg-canvas p-4 shadow-2xl sm:p-5">
        <div className="flex items-center gap-2 text-danger-text">
          <Icon name="emergency" size={20} />
          <p className="text-sm font-bold uppercase tracking-wide">
            Emergency detected
          </p>
        </div>

        {/* The avatar speaks the engine's escalation and the first-aid script
            with no interaction at all — the reason this path exists. */}
        <div className="mt-3">
          <DoctorAvatar
            message={alert.escalationMessage}
            variant="emergency"
            urgency={alert.urgency}
            ruleIds={alert.ruleIds}
            language={language}
            autoSpeak
            autoFirstAid
          />
        </div>

        <a
          href={`tel:${alert.ambulanceNumber}`}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-2xl bg-danger-text px-4 py-4 text-base font-bold text-white shadow-lg active:scale-[0.99]"
        >
          <Icon name="phone" size={20} />
          Call {alert.ambulanceNumber} — {alert.ambulanceName}
        </a>

        <div className="mt-3 rounded-2xl border border-line bg-surface p-3">
          <p className="text-xs font-semibold text-ink-900">
            What SuwaPath has already done
          </p>
          <ul className="mt-1.5 space-y-1 text-xs text-ink-700">
            <li className="flex items-start gap-1.5">
              <Icon name="hospital" size={13} className="mt-0.5 shrink-0" />
              {alert.alreadyActive
                ? "The nearest emergency departments were already alerted a few minutes ago for this."
                : alert.hospitalsAlerted > 0
                  ? `${alert.hospitalsAlerted} nearby emergency department(s) have been alerted that you may be coming.`
                  : "No nearby emergency department could be reached automatically. Call the ambulance."}
            </li>
            <li className="flex items-start gap-1.5">
              <Icon name="group" size={13} className="mt-0.5 shrink-0" />
              {alert.alreadyActive
                ? "Your emergency contacts have already been told."
                : alert.guardiansNotified > 0
                  ? `${alert.guardiansNotified} family contact(s) have been told.`
                  : "No family contact has emergency alerts turned on, so nobody was told."}
            </li>
          </ul>
          <p className="mt-2 border-t border-line pt-2 text-[11px] text-ink-500">
            SuwaPath cannot dial the ambulance for you. Alerting a hospital is
            not the same as an ambulance being on its way — make the call.
          </p>
        </div>

        {alert.hospitals.length > 0 && (
          <div className="mt-3 rounded-2xl border border-line bg-surface p-3">
            {/* Not "nearest": these are ranked by whether the facility can
                actually treat what fired — a hospital 4 km away with a
                cardiology unit and an ECG outranks a bare emergency room at
                2 km for chest pain. Every row shows its distance so the
                ordering is never a hidden claim. */}
            <p className="text-xs font-semibold text-ink-900">
              Emergency departments equipped for this
            </p>
            <ul className="mt-1">
              {alert.hospitals.slice(0, 4).map((facility) => (
                <FacilityRow key={facility.hospital_id} facility={facility} />
              ))}
            </ul>
          </div>
        )}

        <div className="mt-3 rounded-2xl border border-line bg-surface p-3">
          <p className="text-xs font-semibold text-ink-900">Why this fired</p>
          <ul className="mt-1.5 space-y-1 text-xs text-ink-700">
            {alert.rules.slice(0, 3).map((rule) => (
              <li key={rule.rule_id}>
                <span className="font-medium text-ink-900">{rule.label}</span>
                {" — "}
                {rule.rationale}
              </li>
            ))}
          </ul>
          <p className="mt-2 border-t border-line pt-2 text-[11px] text-ink-500">
            Heard as: “{alert.heard}”. Voice recognition mishears — if that is
            not what you said, this is a false alarm.
          </p>
        </div>

        <button
          type="button"
          onClick={onDismiss}
          className="sp-btn sp-btn-ghost mt-3 w-full !py-3 text-sm"
        >
          I'm safe — close this and keep listening
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Toggle                                                             */
/* ------------------------------------------------------------------ */

export default function EmergencyListener() {
  const { user } = useAuth();
  const location = useLocation();
  const emergency = useEmergencyVoice();
  const [panelOpen, setPanelOpen] = useState(false);
  const [readiness, setReadiness] = useState<Readiness | null>(null);

  const isPatient = user?.role === "patient";
  const language = user?.preferred_language ?? "en";

  useEffect(() => {
    if (!isPatient || !panelOpen || readiness) return;
    api
      .get("/emergency/readiness")
      .then(({ data }) => setReadiness(data))
      .catch(() => {
        /* the toggle still works without the explanation */
      });
  }, [isPatient, panelOpen, readiness]);

  // Closing the panel on navigation, but never the alert — an emergency
  // overlay must outlive a mistaken tap on a nav tab.
  useEffect(() => setPanelOpen(false), [location.pathname]);

  if (!isPatient) return null;

  const { armed, listening, alert } = emergency;
  const guardians = readiness?.guardians_who_would_be_told ?? [];

  return (
    <>
      {alert && (
        <EmergencyOverlay
          alert={alert}
          language={language}
          onDismiss={emergency.dismissAlert}
        />
      )}

      {panelOpen && (
        <div className="fixed inset-x-3 bottom-36 z-[65] mx-auto max-w-sm rounded-2xl border border-line bg-surface p-4 shadow-2xl lg:inset-x-auto lg:bottom-24 lg:left-8">
          <div className="flex items-start justify-between gap-2">
            <p className="text-sm font-bold text-ink-900">Hands-free emergency help</p>
            <button
              type="button"
              onClick={() => setPanelOpen(false)}
              aria-label="Close"
              className="shrink-0 text-ink-500"
            >
              <Icon name="close" size={16} />
            </button>
          </div>

          <p className="mt-2 text-xs leading-relaxed text-ink-700">
            SuwaPath listens for emergency warning signs and acts without you
            touching the screen. Say what is happening — “I have chest pain and
            I cannot breathe” — and it will alert the nearest emergency
            departments, tell your family contacts, and read the first-aid steps
            aloud.
          </p>

          {!emergency.supported ? (
            <p className="mt-3 rounded-xl bg-canvas px-3 py-2 text-xs text-ink-600">
              This browser cannot listen. Open SuwaPath in Chrome or Safari to
              use hands-free help.
            </p>
          ) : (
            <>
              <ul className="mt-3 space-y-1.5 text-xs text-ink-700">
                <li className="flex items-start gap-1.5">
                  <Icon name="phone" size={13} className="mt-0.5 shrink-0" />
                  Nothing is dialled for you. {readiness?.ambulance_number ?? "1990"}{" "}
                  {readiness?.ambulance_name ?? "Suwa Seriya"} is put one tap away.
                </li>
                <li className="flex items-start gap-1.5">
                  <Icon name="group" size={13} className="mt-0.5 shrink-0" />
                  {guardians.length > 0
                    ? `Would be told: ${guardians
                        .map((g) => `${g.name} (${g.relationship})`)
                        .join(", ")}.`
                    : "No family contact has emergency alerts turned on, so nobody would be told."}
                </li>
                <li className="flex items-start gap-1.5">
                  <Icon name="privacy" size={13} className="mt-0.5 shrink-0" />
                  Recognition happens in your browser. SuwaPath never keeps a
                  recording, and sentences that are not an emergency are not
                  stored at all.
                </li>
              </ul>

              <button
                type="button"
                onClick={() => (armed ? emergency.disarm() : emergency.arm())}
                className={`mt-3 w-full ${armed ? "sp-btn sp-btn-ghost" : "sp-btn sp-btn-primary"} !py-2.5 text-sm`}
              >
                <Icon name={armed ? "stop" : "mic"} size={15} />
                {armed ? "Stop listening" : "Start listening"}
              </button>
            </>
          )}

          {emergency.error && (
            <p className="mt-2 rounded-xl bg-canvas px-3 py-2 text-xs text-danger-text">
              {emergency.error}
            </p>
          )}
        </div>
      )}

      <button
        type="button"
        onClick={() => setPanelOpen((open) => !open)}
        aria-pressed={armed}
        aria-label={
          armed
            ? "Hands-free emergency help is listening"
            : "Set up hands-free emergency help"
        }
        className={`fixed bottom-24 left-4 z-[60] flex items-center gap-2 rounded-full border px-3 py-2 shadow-lg transition active:scale-95 lg:bottom-8 lg:left-8 ${
          armed
            ? "border-danger-border bg-surface text-danger-text"
            : "border-line bg-surface text-ink-600"
        }`}
      >
        <Icon name={armed ? "mic" : "emergency"} size={18} />
        {armed && (
          <>
            <span
              className={`h-2 w-2 rounded-full bg-danger-text ${listening ? "animate-pulse" : "opacity-40"}`}
            />
            <span className="text-xs font-semibold">
              {listening ? "Listening" : "Starting…"}
            </span>
          </>
        )}
      </button>
    </>
  );
}
