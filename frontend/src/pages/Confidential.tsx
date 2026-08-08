import { useState } from "react";
import { Link } from "react-router-dom";
import { Brand, Card, Chip, ErrorNote, Icon, Spinner, UrgencyBadge } from "../components/ui";
import { api, errorMessage } from "../lib/api";

/** Confidential mode runs entirely outside the authenticated app: no account,
 *  no link to a patient record, and the session id lives only in component
 *  state plus the recovery code the user saves themselves. */
export default function Confidential() {
  const [stage, setStage] = useState<"intro" | "questions" | "result">("intro");
  const [session, setSession] = useState<any>(null);
  const [answers, setAnswers] = useState<Record<string, any>>({});
  const [facilities, setFacilities] = useState<any[]>([]);
  const [resumeCode, setResumeCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleted, setDeleted] = useState(false);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post("/confidential/sessions", {
        language: "en",
        approximate_city: "Colombo",
        latitude: 6.9271,
        longitude: 79.8612,
      });
      setSession(data);
      setStage("questions");
    } catch (err) {
      setError(errorMessage(err, "Could not start a private session."));
    } finally {
      setBusy(false);
    }
  }

  async function resume() {
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post("/confidential/sessions/resume", {
        recovery_code: resumeCode,
      });
      setSession(data);
      setAnswers(data.answers ?? {});
      setStage(data.testing_guidance ? "result" : "questions");
      if (data.testing_guidance) await loadFacilities(data.session_id);
    } catch (err) {
      setError(errorMessage(err, "That recovery code was not recognised."));
    } finally {
      setBusy(false);
    }
  }

  async function loadFacilities(sessionId: string) {
    const { data } = await api.get(
      `/confidential/sessions/${sessionId}/facilities`,
    );
    setFacilities(data.results);
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post(
        `/confidential/sessions/${session.session_id}/answers`,
        { answers },
      );
      setSession({ ...session, ...data });
      await loadFacilities(session.session_id);
      setStage("result");
    } catch (err) {
      setError(errorMessage(err, "Could not save your answers."));
    } finally {
      setBusy(false);
    }
  }

  async function deleteSession() {
    if (!confirm("Delete this private session and everything in it? This cannot be undone.")) return;
    setBusy(true);
    try {
      await api.delete(`/confidential/sessions/${session.session_id}`);
      setDeleted(true);
      setSession(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-programme-surface to-canvas">
      <header className="border-b border-ink-100 bg-white/80 backdrop-blur">
        <div className="max-w-3xl mx-auto flex items-center justify-between px-4 py-3">
          <Brand />
          <Link to="/login" className="text-sm text-ink-600 hover:underline">
            Back to sign in
          </Link>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-8 space-y-5">
        {deleted ? (
          <Card>
            <div className="text-center py-8">
              <Icon name="delete" size={44} className="mx-auto text-ink-400" />
              <h2 className="text-xl font-bold text-ink-900 mt-3">
                Session deleted
              </h2>
              <p className="text-ink-600 mt-1">
                Everything from that private session has been removed. The
                recovery code no longer works.
              </p>
              <button className="sp-btn sp-btn-primary mt-5" onClick={() => setDeleted(false)}>
                Start a new private session
              </button>
            </div>
          </Card>
        ) : stage === "intro" ? (
          <>
            <Card>
              <div className="text-center py-4">
                <Icon name="privacy" size={40} className="mx-auto text-programme-text" />
                <h1 className="text-2xl font-bold text-ink-900 mt-3">
                  Confidential Sexual Health
                </h1>
                <p className="text-ink-600 mt-2 max-w-lg mx-auto">
                  Private, judgement-free guidance. No account needed, and
                  nothing here is linked to your SuwaPath profile or shared with
                  family members.
                </p>
              </div>

              <ul className="mt-4 space-y-2.5 max-w-md mx-auto">
                {[
                  "No name, email or phone number required",
                  "Answers are never attached to your normal medical record",
                  "You get a recovery code to return later",
                  "You can delete everything at any time",
                ].map((item) => (
                  <li key={item} className="flex items-start gap-2.5 text-sm text-ink-700">
                    <Icon name="check" size={16} className="text-programme-text mt-0.5" />
                    {item}
                  </li>
                ))}
              </ul>

              <ErrorNote message={error} />
              <button
                className="sp-btn sp-btn-solid-programme w-full mt-6"
                onClick={() => void start()}
                disabled={busy}
              >
                {busy ? "Starting…" : "Continue Privately"}
              </button>
            </Card>

            <Card title="Returning to a previous session?">
              <div className="flex gap-2">
                <input
                  className="sp-input font-mono uppercase"
                  placeholder="SUWA-XXXX-XXXX"
                  value={resumeCode}
                  onChange={(event) => setResumeCode(event.target.value)}
                />
                <button
                  className="sp-btn sp-btn-secondary"
                  onClick={() => void resume()}
                  disabled={busy || !resumeCode.trim()}
                >
                  Resume
                </button>
              </div>
            </Card>
          </>
        ) : stage === "questions" ? (
          <>
            {session?.recovery_code && (
              <div className="rounded-2xl border-2 border-programme-border bg-programme-surface p-5">
                <p className="font-bold text-programme-text">
                  Save your recovery code now
                </p>
                <p className="text-2xl font-mono font-bold text-ink-900 mt-2 tracking-wider">
                  {session.recovery_code}
                </p>
                <p className="text-sm text-ink-700 mt-2">{session.notice}</p>
              </div>
            )}

            <Card title="A few private questions">
              <div className="space-y-5">
                {session.questions.map((question: any) => (
                  <div key={question.code}>
                    <p className="font-medium text-ink-900 mb-2">
                      {question.label}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {question.options.map((option: string) => {
                        const selected =
                          question.type === "multi"
                            ? (answers[question.code] ?? []).includes(option)
                            : answers[question.code] === option;
                        return (
                          <button
                            key={option}
                            onClick={() =>
                              setAnswers((previous) => {
                                if (question.type === "multi") {
                                  const current: string[] = previous[question.code] ?? [];
                                  return {
                                    ...previous,
                                    [question.code]: current.includes(option)
                                      ? current.filter((o) => o !== option)
                                      : [...current, option],
                                  };
                                }
                                return { ...previous, [question.code]: option };
                              })
                            }
                            className={`rounded-xl border px-3.5 py-2 text-sm transition ${
                              selected
                                ? "border-programme-border bg-programme-surface text-programme-text font-medium"
                                : "border-ink-200 text-ink-600 hover:border-programme-border"
                            }`}
                          >
                            {option}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>

              <ErrorNote message={error} />
              <button
                className="sp-btn sp-btn-solid-programme w-full mt-6"
                onClick={() => void submit()}
                disabled={busy || Object.keys(answers).length === 0}
              >
                {busy ? "Working…" : "Get confidential guidance"}
              </button>
            </Card>
          </>
        ) : (
          <>
            <Card
              title="Your confidential guidance"
              action={session.urgency && <UrgencyBadge urgency={session.urgency} />}
            >
              <p className="text-ink-700">{session.testing_guidance}</p>

              {session.recommended_tests?.length > 0 && (
                <div className="mt-4">
                  <p className="text-sm font-semibold text-ink-800 mb-2">
                    Suggested tests
                  </p>
                  <div className="space-y-2">
                    {session.recommended_tests.map((test: any) => (
                      <div
                        key={test.code}
                        className="flex items-center justify-between rounded-xl border border-ink-100 px-3.5 py-2.5"
                      >
                        <span className="text-sm text-ink-800">{test.name}</span>
                        <span className="text-sm text-ink-500">
                          ~LKR {test.typical_price_lkr?.toLocaleString()}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </Card>

            <Card title="Confidential services near you">
              {facilities.length === 0 ? (
                <Spinner />
              ) : (
                <div className="space-y-3">
                  {facilities.map((facility) => (
                    <div
                      key={facility.hospital_id}
                      className="rounded-xl border border-ink-100 p-3.5"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="font-semibold text-ink-900">
                            {facility.name}
                          </p>
                          <p className="text-sm text-ink-500">
                            {facility.city} · {facility.distance_km} km
                          </p>
                        </div>
                        {facility.offers_confidential_testing && (
                          <Chip tone="programme">Confidential testing</Chip>
                        )}
                      </div>
                      <p className="text-sm text-ink-600 mt-2">
                        {facility.explanation}
                      </p>
                      {facility.phone && (
                        <p className="text-sm text-brand-700 font-medium mt-1.5">
                          {facility.phone}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card title="Your privacy">
              <div className="space-y-3">
                <div className="rounded-xl bg-ink-50 p-3.5">
                  <p className="text-xs text-ink-500">Recovery code</p>
                  <p className="font-mono font-bold text-ink-900">
                    {session.recovery_code ?? "Saved earlier — keep it safe"}
                  </p>
                </div>
                <p className="text-sm text-ink-600">
                  This session holds no name, email or phone number and is not
                  connected to any SuwaPath account.
                </p>
                <button
                  className="sp-btn sp-btn-danger sp-btn-block"
                  onClick={() => void deleteSession()}
                  disabled={busy}
                >
                  Delete this private session
                </button>
              </div>
            </Card>
          </>
        )}
      </main>
    </div>
  );
}
