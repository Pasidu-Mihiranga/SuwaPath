/**
 * Send a patient a message they will hear read aloud.
 *
 * The patient app lifts a care-team message out of the notification list and
 * speaks it through the avatar. That is the point: a patient who cannot
 * comfortably read the screen — because they read Sinhala better than
 * English, or read little at all — still gets what their doctor told them.
 *
 * The doctor's words are sent verbatim. Nothing rewrites, summarises or
 * expands them. The avatar's rule across the whole product is that it only
 * voices deterministic text, and a clinician's own sentence is exactly that;
 * a model "improving" the phrasing would make the doctor accountable for
 * words they never wrote.
 */

import { useState } from "react";
import { Card, ErrorNote, Icon } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";

const MAX = 1200;

/** Phrases a doctor sends constantly, as a starting point rather than a send. */
const STARTERS = [
  "Your test results came back normal. Nothing further is needed for now.",
  "I'd like to see you again — please book a follow-up appointment.",
  "Please continue the treatment as prescribed and finish the full course.",
  "Your results need a closer look. Please come in as soon as you can.",
];

export default function MessagePatient({
  patientUserId,
  patientName,
}: {
  patientUserId: string;
  patientName?: string;
}) {
  const [body, setBody] = useState("");
  const [urgent, setUrgent] = useState(false);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    const text = body.trim();
    if (text.length < 4) {
      setError("Write the message you want them to hear.");
      return;
    }
    setSending(true);
    setError(null);
    try {
      await api.post("/doctor/patients/message", {
        patient_user_id: patientUserId,
        body: text,
        urgent,
      });
      setSent(true);
      setBody("");
      setUrgent(false);
    } catch (err) {
      setError(errorMessage(err, "The message could not be sent."));
    } finally {
      setSending(false);
    }
  }

  return (
    <Card
      title="Send a message"
      subtitle={
        patientName
          ? `${patientName} will see this and can have it read aloud.`
          : "The patient will see this and can have it read aloud."
      }
    >
      <ErrorNote message={error} />

      {sent && (
        <div className="mb-3 flex items-start gap-2 rounded-xl border border-line bg-programme-surface p-3">
          <Icon name="verified" size={16} className="mt-0.5 shrink-0 text-programme-text" />
          <p className="text-sm text-programme-text">
            Sent. It appears at the top of their notifications, and the
            assistant can read it out in their own language setting.
          </p>
        </div>
      )}

      <div className="mb-2 flex flex-wrap gap-1.5">
        {STARTERS.map((phrase) => (
          <button
            key={phrase}
            type="button"
            onClick={() => { setBody(phrase); setSent(false); }}
            className="rounded-lg border border-line px-2.5 py-1 text-left text-[11px] text-ink-600 transition hover:border-brand-300 hover:bg-brand-50"
          >
            {phrase.length > 46 ? `${phrase.slice(0, 46)}…` : phrase}
          </button>
        ))}
      </div>

      <textarea
        className="sp-input min-h-[110px] w-full resize-y text-sm"
        placeholder="Write in the words you'd use with them. It will be read aloud exactly as typed."
        maxLength={MAX}
        value={body}
        onChange={(event) => { setBody(event.target.value); setSent(false); }}
      />

      <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm text-ink-700">
          <input
            type="checkbox"
            checked={urgent}
            onChange={(event) => setUrgent(event.target.checked)}
            className="h-4 w-4 accent-[var(--color-danger-text)]"
          />
          Mark as needing attention today
        </label>
        <div className="flex items-center gap-3">
          <span className="text-xs text-ink-400 tabular-nums">
            {body.length}/{MAX}
          </span>
          <button
            type="button"
            onClick={() => void send()}
            disabled={sending || body.trim().length < 4}
            className="sp-btn sp-btn-primary sp-btn-sm"
          >
            <Icon name="send" size={14} />
            {sending ? "Sending…" : "Send"}
          </button>
        </div>
      </div>

      <p className="mt-2 text-[11px] text-ink-500">
        Sent word for word. Nothing rewrites it, and marking it urgent changes
        only how prominently it appears — clinical urgency stays with the rule
        engine.
      </p>
    </Card>
  );
}
