/**
 * A speaking face for the moments that matter most.
 *
 * Why this exists at all: a patient who cannot read the screen — because they
 * read Sinhala better than they read English, because they read little at all,
 * or because they are frightened and the words are not going in — gets nothing
 * from a well-formatted markdown block. Spoken guidance with something to look
 * at is the accessible form of the same information.
 *
 * What it is allowed to say is strictly bounded, and that boundary is the
 * feature. It speaks only:
 *
 *   1. the escalation message the deterministic rule engine produced,
 *   2. a hand-written first-aid script from `firstAid.ts`,
 *   3. text a doctor typed.
 *
 * It never speaks model output. The rest of the platform already holds that
 * urgency is not negotiable by conversation; this applies the same rule to the
 * spoken channel, where a calm human voice makes anything it says sound far
 * more authoritative than text on a screen.
 *
 * Degrading honestly: browsers frequently have no Sinhala or Tamil voice
 * installed. Rather than reading Sinhala aloud with an English voice — which
 * produces confident nonsense — it says so and shows the text instead. The
 * existing voice module set that standard: "a microphone icon that does
 * nothing is worse than no microphone icon."
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Icon from "../Icon";
import * as voice from "../../lib/voice";
import { scriptForRules, spokenForm, type FirstAidScript } from "./firstAid";

type Mood = "calm" | "urgent";
type State = "idle" | "speaking";

export interface DoctorAvatarProps {
  /** Deterministic escalation text from the rule engine. */
  message: string;
  /** Red-flag rule ids that fired, most serious first. */
  ruleIds?: string[];
  urgency?: string;
  language?: string;
  /** Who is speaking, when a doctor sent this rather than the engine. */
  speakerName?: string;
  autoSpeak?: boolean;
  onDismiss?: () => void;
}

/* ------------------------------------------------------------------ */
/* The face                                                            */
/* ------------------------------------------------------------------ */

/**
 * Mouth shapes cycled while speaking. Four is enough to read as speech at a
 * glance; real viseme mapping would need phoneme timings the Web Speech API
 * does not expose, so this is deliberately an impression rather than a claim
 * of accuracy.
 */
const MOUTHS = [
  "M 34 46 Q 42 50 50 46",
  "M 34 45 Q 42 56 50 45",
  "M 35 46 Q 42 52 49 46",
  "M 33 46 Q 42 48 51 46",
];

function Face({ state, mouth, mood }: { state: State; mouth: number; mood: Mood }) {
  const ink = mood === "urgent" ? "var(--sp-avatar-urgent)" : "var(--sp-avatar-calm)";
  return (
    <svg viewBox="0 0 84 84" width="76" height="76" role="img"
         aria-label="SuwaPath assistant" className="shrink-0">
      <circle cx="42" cy="42" r="40" fill="currentColor" opacity="0.10" />
      {/* head */}
      <circle cx="42" cy="38" r="24" fill="currentColor" opacity="0.16" />
      {/* eyes — a slow blink keeps it from looking like a frozen graphic */}
      <g fill={ink}>
        <circle cx="34" cy="34" r="2.6">
          <animate attributeName="ry" values="2.6;2.6;0.3;2.6" dur="4.5s"
                   keyTimes="0;0.94;0.97;1" repeatCount="indefinite" />
        </circle>
        <circle cx="50" cy="34" r="2.6">
          <animate attributeName="ry" values="2.6;2.6;0.3;2.6" dur="4.5s"
                   keyTimes="0;0.94;0.97;1" repeatCount="indefinite" />
        </circle>
      </g>
      <path d={state === "speaking" ? MOUTHS[mouth] : MOUTHS[0]}
            stroke={ink} strokeWidth="2.6" strokeLinecap="round" fill="none" />
      {/* stethoscope, so it reads as clinical rather than as a chat mascot */}
      <path d="M 26 52 Q 26 66 42 66 Q 58 66 58 54"
            stroke={ink} strokeWidth="2" fill="none" opacity="0.55" />
      <circle cx="58" cy="52" r="3.4" fill={ink} opacity="0.55" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */

export default function DoctorAvatar({
  message,
  ruleIds = [],
  urgency = "emergency",
  language = "en",
  speakerName,
  autoSpeak = true,
  onDismiss,
}: DoctorAvatarProps) {
  const [state, setState] = useState<State>("idle");
  const [mouth, setMouth] = useState(0);
  const [noVoice, setNoVoice] = useState(false);
  const [showAid, setShowAid] = useState(false);
  const timer = useRef<number | null>(null);
  const started = useRef(false);

  const mood: Mood = urgency === "emergency" || urgency === "urgent" ? "urgent" : "calm";
  const script: FirstAidScript | null = scriptForRules(ruleIds);

  const stopMouth = useCallback(() => {
    if (timer.current !== null) {
      window.clearInterval(timer.current);
      timer.current = null;
    }
    setMouth(0);
  }, []);

  const say = useCallback(
    (text: string) => {
      voice.stopSpeaking();
      stopMouth();
      setNoVoice(false);
      voice.speak(text, {
        language,
        onStart: () => {
          setState("speaking");
          // Boundary events are not emitted by every engine, so the mouth
          // runs on a timer and boundaries only nudge it. Driving it purely
          // from boundaries leaves the face frozen mid-sentence on the
          // engines that stay silent.
          timer.current = window.setInterval(
            () => setMouth((m) => (m + 1) % MOUTHS.length),
            130,
          );
        },
        onBoundary: () => setMouth((m) => (m + 1) % MOUTHS.length),
        onEnd: () => {
          stopMouth();
          setState("idle");
        },
        onUnavailable: () => {
          setNoVoice(true);
          setState("idle");
        },
      });
    },
    [language, stopMouth],
  );

  useEffect(() => {
    if (!autoSpeak || started.current) return;
    started.current = true;
    // Voices load asynchronously in most browsers; asking immediately often
    // finds an empty list and wrongly concludes the language is unsupported.
    const t = window.setTimeout(() => say(message), 220);
    return () => window.clearTimeout(t);
  }, [autoSpeak, message, say]);

  useEffect(() => () => {
    voice.stopSpeaking();
    if (timer.current !== null) window.clearInterval(timer.current);
  }, []);

  const speaking = state === "speaking";

  return (
    <section
      // Assertive: an emergency escalation is exactly the case where a screen
      // reader should interrupt whatever it was reading.
      aria-live={mood === "urgent" ? "assertive" : "polite"}
      className={`rounded-2xl border p-4 ${
        mood === "urgent"
          ? "border-danger-border bg-danger-surface"
          : "border-line bg-surface"
      }`}
      style={{
        // Tokens the SVG reads, so the face follows the surrounding theme
        // rather than carrying hardcoded colours that break in dark mode.
        ["--sp-avatar-urgent" as string]: "var(--color-danger-text, #b3261e)",
        ["--sp-avatar-calm" as string]: "var(--color-brand-700, #0e7c86)",
      }}
    >
      <div className="flex items-start gap-3">
        <div className={mood === "urgent" ? "text-danger-text" : "text-brand-600"}>
          <Face state={state} mouth={mouth} mood={mood} />
        </div>

        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink-900">
            {speakerName ? `${speakerName} says` : "SuwaPath Assistant"}
          </p>
          <p className="mt-1 whitespace-pre-line text-sm leading-relaxed text-ink-800">
            {message}
          </p>

          {noVoice && (
            <p className="mt-2 rounded-lg bg-canvas px-2.5 py-1.5 text-xs text-ink-600">
              Your device has no voice installed for this language, so this is
              shown as text only.
            </p>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => (speaking ? (voice.stopSpeaking(), stopMouth(), setState("idle")) : say(message))}
              className="sp-btn sp-btn-ghost !py-1.5 text-xs"
            >
              <Icon name={speaking ? "close" : "volumeUp"} size={14} />
              {speaking ? "Stop" : "Read aloud"}
            </button>

            {script && (
              <button
                type="button"
                onClick={() => {
                  setShowAid(true);
                  say(spokenForm(script));
                }}
                className="sp-btn sp-btn-primary !py-1.5 text-xs"
              >
                <Icon name="ai" size={14} />
                What to do now
              </button>
            )}

            {onDismiss && (
              <button type="button" onClick={() => { voice.stopSpeaking(); onDismiss(); }}
                      className="sp-btn sp-btn-ghost !py-1.5 text-xs">
                Dismiss
              </button>
            )}
          </div>

          {showAid && script && (
            <div className="mt-3 rounded-xl border border-line bg-surface p-3">
              <p className="text-xs font-semibold text-ink-900">{script.title}</p>
              <ol className="mt-1.5 list-decimal space-y-1 pl-4 text-xs text-ink-800">
                {script.steps.map((step) => <li key={step}>{step}</li>)}
              </ol>
              {script.avoid?.length ? (
                <ul className="mt-2 space-y-1 border-t border-line pt-2 pl-4 text-xs text-danger-text list-disc">
                  {script.avoid.map((item) => <li key={item}>{item}</li>)}
                </ul>
              ) : null}
              <p className="mt-2 text-[11px] text-ink-500">
                General first-aid guidance, not a substitute for the ambulance
                service's instructions. Follow what they tell you.
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
