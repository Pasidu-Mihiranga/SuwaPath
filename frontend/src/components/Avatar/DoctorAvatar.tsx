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

/**
 * Why the avatar appears. Emergencies are the loudest case but not the most
 * common one: the patients who benefit most from spoken guidance are the ones
 * using the elderly and maternal pathways, every day, for ordinary replies.
 * Scoping this to emergencies only would have put the accessibility feature
 * exactly where it is least needed.
 */
export type AvatarVariant = "emergency" | "care" | "doctor";

export interface DoctorAvatarProps {
  /** The text to speak. Never model-generated for the emergency variant. */
  message: string;
  variant?: AvatarVariant;
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
 * glance; real viseme mapping needs phoneme timings the Web Speech API does
 * not expose, so this is deliberately an impression rather than a claim of
 * accuracy. Closed shape first, so a paused avatar rests with a neutral mouth.
 */
const MOUTHS = [
  "M 33 47 Q 42 50 51 47",              // relaxed, near-closed
  "M 33 46 Q 42 57 51 46",              // open, vowel
  "M 34 47 Q 42 53 50 47",              // half
  "M 32 47 Q 42 49 52 47",              // wide, narrow aperture
];

/**
 * SVG SMIL animation is not covered by the global reduced-motion rule.
 *
 * `index.css` neutralises motion by forcing `animation-duration` and
 * `transition-duration` to near zero, which handles CSS but does nothing to
 * an `<animate>` element — those keep running. Since this component exists
 * for elderly and low-literacy users, and vestibular sensitivity is common in
 * exactly that group, the preference is checked here and the animation is
 * simply not rendered.
 */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!query) return;
    setReduced(query.matches);
    const onChange = () => setReduced(query.matches);
    query.addEventListener?.("change", onChange);
    return () => query.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
}

function Face({
  state, mouth, mood, still,
}: { state: State; mouth: number; mood: Mood; still: boolean }) {
  const ink = mood === "urgent" ? "var(--sp-avatar-urgent)" : "var(--sp-avatar-calm)";
  const speaking = state === "speaking";
  return (
    <svg viewBox="0 0 84 84" width="84" height="84" role="img"
         aria-label="SuwaPath assistant" className="shrink-0 overflow-visible">
      <defs>
        <linearGradient id="sp-av-face" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.22" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0.10" />
        </linearGradient>
      </defs>

      {/* A soft pulse while speaking, so the card reads as "talking" from
          across a room — which is the point for someone who is not reading
          it. Held still otherwise: an idle avatar that keeps moving is a
          distraction on a page about someone's health. */}
      {speaking && !still && (
        <circle cx="42" cy="42" r="38" fill="currentColor" opacity="0.10">
          <animate attributeName="r" values="34;40;34" dur="2.4s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="0.14;0.03;0.14" dur="2.4s"
                   repeatCount="indefinite" />
        </circle>
      )}

      <circle cx="42" cy="42" r="33" fill="url(#sp-av-face)" />
      {/* shoulders, so it reads as a person rather than a floating head */}
      <path d="M 16 78 Q 20 60 42 60 Q 64 60 68 78" fill="currentColor" opacity="0.14" />
      <circle cx="42" cy="36" r="21" fill="url(#sp-av-face)" />

      {/* Brows. They sit lower and closer in the urgent state, which is what
          makes the difference legible without changing colour alone —
          colour is not available to every viewer. */}
      <g stroke={ink} strokeWidth="2" strokeLinecap="round" opacity="0.75">
        <path d={mood === "urgent" ? "M 29 27 L 38 29" : "M 29 27 L 38 26"} />
        <path d={mood === "urgent" ? "M 55 27 L 46 29" : "M 55 27 L 46 26"} />
      </g>

      {/* Eyes, with an occasional blink. Without it the face reads as a
          frozen graphic and people stop expecting it to speak. */}
      <g fill={ink}>
        {[35, 49].map((cx) => (
          <ellipse key={cx} cx={cx} cy="34" rx="2.7" ry="2.7">
            {!still && (
              <animate attributeName="ry" values="2.7;2.7;0.3;2.7" dur="5.2s"
                       keyTimes="0;0.93;0.96;1" repeatCount="indefinite" />
            )}
          </ellipse>
        ))}
      </g>

      <path d={speaking ? MOUTHS[mouth] : MOUTHS[0]}
            stroke={ink} strokeWidth="2.4" strokeLinecap="round" fill="none"
            style={still ? undefined : { transition: "d 90ms linear" }} />

      {/* Stethoscope, so it reads as clinical rather than as a chat mascot. */}
      <g stroke={ink} fill="none" opacity="0.5" strokeLinecap="round">
        <path d="M 30 55 Q 30 70 42 70 Q 55 70 55 58" strokeWidth="2" />
      </g>
      <circle cx="55" cy="56" r="3.6" fill={ink} opacity="0.5" />
      <circle cx="55" cy="56" r="1.5" fill="currentColor" opacity="0.35" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */

export default function DoctorAvatar({
  message,
  variant = "emergency",
  ruleIds = [],
  urgency = variant === "emergency" ? "emergency" : "routine",
  language = "en",
  speakerName,
  // Only an emergency speaks unprompted. A care-pathway reply that starts
  // talking on its own every turn is not an accessibility feature, it is a
  // nuisance that gets muted — and then it is not there for the turn that
  // mattered. The button is always visible; the speech is the patient's call.
  autoSpeak = variant === "emergency",
  onDismiss,
}: DoctorAvatarProps) {
  const [state, setState] = useState<State>("idle");
  const [mouth, setMouth] = useState(0);
  const [noVoice, setNoVoice] = useState(false);
  const [showAid, setShowAid] = useState(false);
  const timer = useRef<number | null>(null);
  const started = useRef(false);

  const reducedMotion = usePrefersReducedMotion();
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
          if (reducedMotion) return;
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
    [language, stopMouth, reducedMotion],
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
      className={`overflow-hidden rounded-2xl border p-4 transition-shadow ${
        mood === "urgent"
          ? "border-danger-border bg-danger-surface shadow-md"
          : "border-line bg-surface"
      } ${speaking ? "ring-1 ring-brand-300" : ""}`}
      style={{
        // Tokens the SVG reads, so the face follows the surrounding theme
        // rather than carrying hardcoded colours that break in dark mode.
        ["--sp-avatar-urgent" as string]: "var(--color-danger-text, #b3261e)",
        ["--sp-avatar-calm" as string]: "var(--color-brand-700, #0e7c86)",
      }}
    >
      <div className="flex items-start gap-3">
        <div className={mood === "urgent" ? "text-danger-text" : "text-brand-600"}>
          <Face state={state} mouth={mouth} mood={mood} still={reducedMotion} />
        </div>

        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink-900">
            {speakerName
              ? `${speakerName} says`
              : variant === "doctor"
                ? "A message from your care team"
                : "SuwaPath Assistant"}
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
