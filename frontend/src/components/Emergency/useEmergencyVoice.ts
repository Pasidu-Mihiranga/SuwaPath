/**
 * Always-on listening for the sentence nobody gets to finish typing.
 *
 * The rest of the assistant is deliberately not hands-free: dictation fills
 * the input box and waits, because "I have chest pain" misheard as "I have
 * chest strain" is worth one tap to catch. This is the exception, and the
 * reason it can be an exception is that it cannot start a conversation, book
 * anything, or say anything a model wrote. All it can do is recognise a fixed
 * clinical pattern and summon help.
 *
 * Design notes that are not obvious from the types:
 *
 * **The microphone stops when the avatar starts.** Text-to-speech comes out of
 * the same device the microphone is listening to, so a speaking avatar is
 * heard as a new utterance — and the avatar says "chest pain" out loud. Left
 * running, the listener screens its own voice and re-triggers forever. So an
 * alert closes the microphone, and it reopens only when the patient dismisses
 * the overlay.
 *
 * **Recognition is restarted, not trusted.** Chrome ends a session after
 * silence regardless of `continuous`, and a listener that quietly died an hour
 * ago is worse than one that was never armed, because the patient believes it
 * is there. `onEnd` therefore rearms, with a backoff so a hard failure does
 * not spin.
 *
 * **Screening is serialised.** Utterances arrive faster than a round trip
 * completes, and the interesting case is someone saying five alarming things
 * in ten seconds. Overlapping requests would race to open five overlays, so
 * one is in flight at a time and the newest utterance wins.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import * as voice from "../../lib/voice";

const ARMED_KEY = "suwapath.emergency.listening";

/** Below this, a transcript fragment is noise rather than a sentence. */
const MIN_UTTERANCE_CHARS = 6;

/** Backoff after recognition dies, so a persistent failure does not spin. */
const RESTART_DELAY_MS = 1200;

export interface EmergencyRule {
  rule_id: string;
  label: string;
  category: string;
  urgency: string;
  matched_terms: string[];
  rationale: string;
}

export interface EmergencyFacility {
  hospital_id: string;
  name: string;
  city: string | null;
  address: string | null;
  phone: string | null;
  distance_km: number;
  has_emergency: boolean;
  is_24_hours: boolean;
  explanation: string;
}

export interface EmergencyAlert {
  /** What the listener actually heard. Shown so a mishearing is visible. */
  heard: string;
  urgency: string;
  escalationMessage: string;
  rules: EmergencyRule[];
  ruleIds: string[];
  ambulanceNumber: string;
  ambulanceName: string;
  hospitals: EmergencyFacility[];
  hospitalsAlerted: number;
  guardiansNotified: number;
  dispatched: boolean;
  alreadyActive: boolean;
}

export interface EmergencyVoice {
  supported: boolean;
  armed: boolean;
  listening: boolean;
  /** Last thing heard, for the "yes, it is on" indicator. */
  heard: string;
  checking: boolean;
  error: string | null;
  alert: EmergencyAlert | null;
  arm: () => void;
  disarm: () => void;
  dismissAlert: () => void;
  /** Screen a typed sentence. The same path, without the microphone. */
  check: (text: string, options?: { dispatch?: boolean }) => Promise<void>;
}

export function useEmergencyVoice(): EmergencyVoice {
  const { user } = useAuth();

  const [armed, setArmed] = useState(
    () => localStorage.getItem(ARMED_KEY) === "true",
  );
  const [listening, setListening] = useState(false);
  const [heard, setHeard] = useState("");
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [alert, setAlert] = useState<EmergencyAlert | null>(null);

  const listenerRef = useRef<voice.Listener | null>(null);
  const restartTimer = useRef<number | null>(null);
  const coords = useRef<{ latitude: number; longitude: number } | null>(null);
  const inFlight = useRef(false);
  const lastScreened = useRef("");
  // Read inside callbacks that must not be re-created when an alert opens —
  // recreating them tears down the recognition session.
  const alertOpen = useRef(false);
  alertOpen.current = alert !== null;

  const supported = voice.listeningSupported();
  const language = user?.preferred_language ?? "en";

  /* ---------------------------------------------------------------- */
  /* Screening                                                        */
  /* ---------------------------------------------------------------- */

  const check = useCallback(
    async (text: string, options?: { dispatch?: boolean }) => {
      const transcript = text.trim();
      if (transcript.length < MIN_UTTERANCE_CHARS) return;

      setChecking(true);
      try {
        const { data } = await api.post("/emergency/voice", {
          transcript,
          latitude: coords.current?.latitude ?? null,
          longitude: coords.current?.longitude ?? null,
          dispatch: options?.dispatch ?? true,
        });

        if (!data?.triggered) return;

        setAlert({
          heard: transcript,
          urgency: data.urgency,
          escalationMessage: data.escalation_message ?? "",
          rules: data.rules ?? [],
          ruleIds: (data.rules ?? []).map((r: EmergencyRule) => r.rule_id),
          ambulanceNumber: data.ambulance_number ?? "1990",
          ambulanceName: data.ambulance_name ?? "Suwa Seriya",
          hospitals: data.hospitals ?? [],
          hospitalsAlerted: data.hospitals_alerted ?? 0,
          guardiansNotified: data.guardians_notified ?? 0,
          dispatched: Boolean(data.dispatched),
          alreadyActive: Boolean(data.already_active),
        });
      } catch (err) {
        // A screening failure must be visible. Silence here reads exactly like
        // "nothing was wrong", which is the one thing it must never mean.
        setError(
          errorMessage(
            err,
            "SuwaPath could not check what you said. If this is an emergency, call 1990 now.",
          ),
        );
      } finally {
        setChecking(false);
      }
    },
    [],
  );

  /* ---------------------------------------------------------------- */
  /* The microphone                                                   */
  /* ---------------------------------------------------------------- */

  const stopListening = useCallback(() => {
    if (restartTimer.current !== null) {
      window.clearTimeout(restartTimer.current);
      restartTimer.current = null;
    }
    listenerRef.current?.stop();
    listenerRef.current = null;
    setListening(false);
  }, []);

  const startListening = useCallback(() => {
    if (listenerRef.current || alertOpen.current) return;

    const handle = voice.listen({
      language,
      onStart: () => setListening(true),
      onPartial: setHeard,
      // The accumulated session transcript is not wanted: it grows all day and
      // re-screening it would keep matching an emergency that was already
      // handled an hour ago.
      onFinal: () => {},
      onUtterance: (text) => {
        setHeard(text);
        if (alertOpen.current || inFlight.current) return;
        if (text === lastScreened.current) return;
        lastScreened.current = text;
        inFlight.current = true;
        void check(text).finally(() => {
          inFlight.current = false;
        });
      },
      onError: (message) => {
        // A blocked or missing microphone is not recoverable by retrying, and
        // an indicator that claims to be listening when it is not is the worst
        // possible outcome for this feature. Stand down and say so.
        if (/blocked|found|in use/i.test(message)) {
          setError(message);
          setArmed(false);
          localStorage.setItem(ARMED_KEY, "false");
        }
      },
      onEnd: () => {
        setListening(false);
        listenerRef.current = null;
      },
    });

    if (!handle) {
      setError("This browser cannot listen. Try Chrome or Safari.");
      setArmed(false);
      localStorage.setItem(ARMED_KEY, "false");
      return;
    }
    listenerRef.current = handle;
  }, [check, language]);

  /* ---------------------------------------------------------------- */
  /* Arming                                                           */
  /* ---------------------------------------------------------------- */

  const arm = useCallback(() => {
    setError(null);
    setArmed(true);
    localStorage.setItem(ARMED_KEY, "true");

    // Where the patient is now, not where they live. Best-effort and silent on
    // refusal: the server falls back to the address on file, and a location
    // prompt is not worth blocking the microphone over.
    navigator.geolocation?.getCurrentPosition(
      (position) => {
        coords.current = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        };
      },
      () => {},
      { timeout: 8000, maximumAge: 300_000 },
    );
  }, []);

  const disarm = useCallback(() => {
    setArmed(false);
    localStorage.setItem(ARMED_KEY, "false");
    setHeard("");
    stopListening();
  }, [stopListening]);

  const dismissAlert = useCallback(() => {
    voice.stopSpeaking();
    setAlert(null);
    lastScreened.current = "";
    setHeard("");
  }, []);

  /* ---------------------------------------------------------------- */
  /* Keeping the session alive                                        */
  /* ---------------------------------------------------------------- */

  useEffect(() => {
    if (!armed || !supported) {
      stopListening();
      return;
    }
    // An open alert owns the audio device: the avatar is speaking and the
    // microphone would hear it.
    if (alert) {
      stopListening();
      return;
    }
    if (listening || listenerRef.current) return;

    restartTimer.current = window.setTimeout(startListening, RESTART_DELAY_MS);
    return () => {
      if (restartTimer.current !== null) {
        window.clearTimeout(restartTimer.current);
        restartTimer.current = null;
      }
    };
  }, [armed, supported, alert, listening, startListening, stopListening]);

  useEffect(
    () => () => {
      listenerRef.current?.stop();
      if (restartTimer.current !== null) {
        window.clearTimeout(restartTimer.current);
      }
    },
    [],
  );

  return {
    supported,
    armed,
    listening,
    heard,
    checking,
    error,
    alert,
    arm,
    disarm,
    dismissAlert,
    check,
  };
}
