import { useEffect, useState } from "react";
import {
  Card,
  Empty,
  Icon,
  type IconName,
  ErrorNote,
  EscalationBanner,
  Spinner,
  formatDate,
} from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { programmeIllustration } from "../../lib/illustration";

export default function Programmes() {
  const [enrollments, setEnrollments] = useState<any[]>([]);
  const [catalogue, setCatalogue] = useState<any[]>([]);
  const [maternal, setMaternal] = useState<any>(null);
  const [elderly, setElderly] = useState<any>(null);
  const [medications, setMedications] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    const [enrolled, available, meds] = await Promise.all([
      api.get("/care/enrollments"),
      api.get("/care/programmes").catch(() => ({ data: [] })),
      api.get("/care/medications").catch(() => ({ data: [] })),
    ]);
    setEnrollments(enrolled.data);
    setCatalogue(available.data);
    setMedications(meds.data);

    await Promise.all([
      api.get("/care/maternal").then((r) => setMaternal(r.data)).catch(() => undefined),
      api.get("/care/elderly").then((r) => setElderly(r.data)).catch(() => undefined),
    ]);
    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, []);

  if (loading) return <Spinner />;

  const enrolledCodes = new Set(enrollments.map((e) => e.programme_code));

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Care Programmes</h1>
        <p className="text-ink-500">
          Continuous support for pregnancy, elderly care and confidential health.
        </p>
      </header>

      {maternal && (
        <MaternalPanel data={maternal} onCheckedIn={load} />
      )}
      {elderly && (
        <ElderlyPanel
          data={elderly}
          medications={medications}
          onChanged={load}
        />
      )}

      <ProgrammeCatalogue
        catalogue={catalogue}
        enrolledCodes={enrolledCodes}
        onEnrolled={load}
      />
    </div>
  );
}

/* ------------------------------------------------------------- Enrolment */

const PROGRAMME_STYLE: Record<string, { icon: IconName; accent: string }> = {
  maternal: { icon: "pregnancy", accent: "sp-gradient-maternal" },
  postpartum: { icon: "pregnancy", accent: "sp-gradient-maternal" },
  elderly: { icon: "elderly", accent: "sp-gradient-elderly" },
  sexual_health: { icon: "privacy", accent: "sp-gradient-programme" },
};

function ProgrammeCatalogue({
  catalogue,
  enrolledCodes,
  onEnrolled,
}: {
  catalogue: any[];
  enrolledCodes: Set<string>;
  onEnrolled: () => void;
}) {
  const [joining, setJoining] = useState<any>(null);
  if (catalogue.length === 0) return null;

  const available = catalogue.filter((p) => !enrolledCodes.has(p.code));

  return (
    <>
      <Card
        title={enrolledCodes.size > 0 ? "Other programmes" : "Available programmes"}
        subtitle="Join a pathway to get reminders, check-ins and continuous follow-up."
      >
        {available.length === 0 ? (
          <Empty
            title="You are enrolled in every available programme"
            hint="Your active pathways are shown above."
          />
        ) : (
          <div className="grid sm:grid-cols-2 gap-3">
            {available.map((programme) => {
              const style =
                PROGRAMME_STYLE[programme.programme_type] ?? {
                  icon: "favorite" as IconName,
                  accent: "",
                };
              // The confidential pathway is deliberately not joinable from an
              // authenticated account — that would defeat its purpose. It is
              // reached anonymously instead (spec §16).
              const isConfidential = programme.programme_type === "sexual_health";
              return (
                <div
                  key={programme.code}
                  className={`relative overflow-hidden rounded-xl border border-line p-4 ${style.accent}`}
                >
                  {/* Decorative. The confidential pathway returns none, which
                      is the point — see lib/illustration.ts. */}
                  {programmeIllustration(programme.programme_type) && (
                    <img
                      src={programmeIllustration(programme.programme_type)!}
                      alt=""
                      aria-hidden="true"
                      loading="lazy"
                      className="pointer-events-none mb-3 h-20 w-auto select-none object-contain"
                    />
                  )}
                  <div className="relative flex items-start gap-3">
                    <span className="sp-icon-tile bg-surface text-brand-700">
                      <Icon name={style.icon} size={20} />
                    </span>
                    <div className="min-w-0">
                      <p className="font-semibold text-ink-900">{programme.name}</p>
                      <p className="text-sm text-ink-600 mt-0.5">
                        {programme.description}
                      </p>
                    </div>
                  </div>
                  {isConfidential ? (
                    <a href="/private" className="sp-btn sp-btn-secondary sp-btn-sm mt-3 relative">
                      <Icon name="privacy" size={15} />
                      Continue privately
                    </a>
                  ) : (
                    <button
                      className="sp-btn sp-btn-primary sp-btn-sm mt-3 relative"
                      onClick={() => setJoining(programme)}
                    >
                      Join programme
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {joining && (
        <EnrolDialog
          programme={joining}
          onClose={() => setJoining(null)}
          onDone={() => {
            setJoining(null);
            onEnrolled();
          }}
        />
      )}
    </>
  );
}

function EnrolDialog({
  programme,
  onClose,
  onDone,
}: {
  programme: any;
  onClose: () => void;
  onDone: () => void;
}) {
  const [edd, setEdd] = useState("");
  const [lmp, setLmp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsDates = programme.programme_type === "maternal";

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.post("/care/enrollments", {
        programme_code: programme.code,
        expected_delivery_date: needsDates && edd ? edd : undefined,
        last_menstrual_period: needsDates && lmp ? lmp : undefined,
      });
      onDone();
    } catch (err) {
      setError(errorMessage(err, "Could not join this programme."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink-900/50 p-4">
      <div className="w-full max-w-md sp-card p-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-lg font-bold text-ink-900">{programme.name}</h3>
            <p className="text-sm text-ink-500 mt-0.5">{programme.description}</p>
          </div>
          <button
            onClick={onClose}
            className="sp-btn sp-btn-ghost sp-btn-sm !px-2"
            aria-label="Close"
          >
            <Icon name="close" size={18} />
          </button>
        </div>

        {needsDates && (
          <div className="mt-4 space-y-3">
            <div>
              <label className="sp-field" htmlFor="edd">
                Expected delivery date
              </label>
              <input
                id="edd"
                type="date"
                className="sp-input"
                value={edd}
                onChange={(event) => setEdd(event.target.value)}
              />
            </div>
            <div>
              <label className="sp-field" htmlFor="lmp">
                First day of last period (optional)
              </label>
              <input
                id="lmp"
                type="date"
                className="sp-input"
                value={lmp}
                onChange={(event) => setLmp(event.target.value)}
              />
            </div>
            <p className="text-xs text-ink-500">
              Used to calculate your pregnancy week and schedule antenatal
              reminders. You can change it later.
            </p>
          </div>
        )}

        {error && (
          <div className="mt-4">
            <ErrorNote message={error} />
          </div>
        )}

        <div className="mt-5 flex gap-2">
          <button className="sp-btn sp-btn-secondary flex-1" onClick={onClose}>
            Cancel
          </button>
          <button
            className="sp-btn sp-btn-primary flex-1"
            onClick={() => void submit()}
            disabled={busy || (needsDates && !edd)}
          >
            {busy ? "Joining…" : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- Maternal */

const MATERNAL_QUESTIONS = [
  { code: "severe_headache", label: "Severe headache?" },
  { code: "blurred_vision", label: "Blurred vision or seeing spots?" },
  { code: "vaginal_bleeding", label: "Any bleeding?" },
  { code: "severe_abdominal_pain", label: "Severe abdominal pain?" },
  { code: "reduced_fetal_movement", label: "Reduced baby movement?" },
  { code: "leaking_fluid", label: "Any fluid leaking?" },
  { code: "swelling", label: "Sudden swelling of face or hands?" },
  { code: "fever", label: "Fever?" },
];

const POSTPARTUM_QUESTIONS = [
  { code: "severe_bleeding", label: "Heavy bleeding (soaking a pad in an hour)?" },
  { code: "fever", label: "Fever or chills?" },
  { code: "severe_headache", label: "Severe headache?" },
  { code: "low_mood", label: "Feeling persistently low or unable to cope?" },
  { code: "child_not_feeding", label: "Is the baby feeding poorly?" },
];

function MaternalPanel({ data, onCheckedIn }: { data: any; onCheckedIn: () => void }) {
  const [answers, setAnswers] = useState<Record<string, boolean>>({});
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const questions = data.is_postpartum ? POSTPARTUM_QUESTIONS : MATERNAL_QUESTIONS;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const { data: response } = await api.post("/care/check-ins", {
        check_in_type: data.is_postpartum ? "postpartum" : "maternal",
        wellbeing: Object.values(answers).some(Boolean) ? "not_great" : "good",
        responses: answers,
      });
      setResult(response);
      onCheckedIn();
    } catch (err) {
      setError(errorMessage(err, "Could not submit your check-in."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="sp-card sp-gradient-maternal p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-sm text-maternal-text font-semibold">
              {data.is_postpartum ? "Postpartum & Newborn Care" : "Maternal Care"}
            </p>
            {!data.is_postpartum ? (
              <>
                <p className="text-3xl font-bold text-ink-900 mt-1">
                  Week {data.pregnancy_week}
                </p>
                <p className="text-ink-600 text-sm">
                  Expected delivery {formatDate(data.expected_delivery_date)}
                </p>
              </>
            ) : (
              <>
                <p className="text-2xl font-bold text-ink-900 mt-1">
                  {data.newborn?.name ?? "Newborn"}
                </p>
                <p className="text-ink-600 text-sm">
                  Born {formatDate(data.newborn?.date_of_birth)} ·{" "}
                  {data.newborn?.birth_weight_kg} kg
                </p>
              </>
            )}
          </div>
          <div className="flex gap-2 flex-wrap">
            {data.is_high_risk && (
              <span className="sp-chip sp-chip-warn">High risk</span>
            )}
            {data.risk_conditions?.map((condition: string) => (
              <span key={condition} className="sp-chip sp-chip-programme">
                {condition}
              </span>
            ))}
          </div>
        </div>

        {data.upcoming_milestone && (
          <div className="mt-4 rounded-xl bg-surface border border-maternal-border p-3">
            <p className="text-xs text-ink-500">Upcoming</p>
            <p className="font-semibold text-ink-900">
              {data.upcoming_milestone.label}
              {data.upcoming_milestone.week ? ` — week ${data.upcoming_milestone.week}` : ""}
            </p>
          </div>
        )}
      </div>

      {data.epds_score != null && (
        <Card title="Mental wellbeing screening">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-ink-500">EPDS score</p>
              <p className="text-2xl font-bold text-ink-900">{data.epds_score}</p>
            </div>
            <p className="text-sm text-ink-600 max-w-sm text-right">
              {data.epds_interpretation}
            </p>
          </div>
        </Card>
      )}

      <Card
        title="Daily check-in"
        subtitle="Tell us about any warning signs. This takes under a minute."
      >
        {result ? (
          <div className="space-y-3">
            <EscalationBanner
              urgency={result.urgency}
              message={result.escalation_message}
            />
            {!result.triggered_alert && (
              <div className="sp-notice sp-notice-ok flex-col">
                <p className="font-semibold">Check-in recorded</p>
                <p className="text-sm text-ink-700 mt-0.5">
                  No danger signs reported. Keep monitoring how you feel.
                </p>
              </div>
            )}
            {result.guardian_alerts_raised > 0 && (
              <p className="text-sm text-ink-600">
                {result.guardian_alerts_raised} authorised family contact(s) were
                notified.
              </p>
            )}
            <button className="sp-btn sp-btn-secondary w-full" onClick={() => setResult(null)}>
              Record another check-in
            </button>
          </div>
        ) : (
          <>
            <div className="grid sm:grid-cols-2 gap-2.5">
              {questions.map((question) => (
                <label
                  key={question.code}
                  className={`flex items-center gap-3 rounded-xl border p-3.5 cursor-pointer transition ${
                    answers[question.code]
                      ? "border-danger-border bg-danger-surface"
                      : "border-ink-200 hover:border-brand-400"
                  }`}
                >
                  <input
                    type="checkbox"
                    className="h-5 w-5 accent-danger-solid"
                    checked={Boolean(answers[question.code])}
                    onChange={(event) =>
                      setAnswers((previous) => ({
                        ...previous,
                        [question.code]: event.target.checked,
                      }))
                    }
                  />
                  <span className="text-sm text-ink-800">{question.label}</span>
                </label>
              ))}
            </div>
            <ErrorNote message={error} />
            <button
              className="sp-btn sp-btn-primary w-full mt-4"
              onClick={() => void submit()}
              disabled={busy}
            >
              {busy ? "Submitting…" : "Submit check-in"}
            </button>
          </>
        )}
      </Card>

      {data.blood_pressure?.length > 0 && (
        <Card title="Blood pressure history">
          <div className="flex gap-2 overflow-x-auto pb-2">
            {data.blood_pressure.slice(0, 10).map((entry: any, index: number) => (
              <div
                key={index}
                className={`shrink-0 rounded-xl border p-3 text-center min-w-[92px] ${
                  entry.is_abnormal
                    ? "border-warn-border bg-warn-surface"
                    : "border-ink-200"
                }`}
              >
                <p className="font-bold text-ink-900">
                  {entry.systolic}/{entry.diastolic}
                </p>
                <p className="text-[11px] text-ink-500">
                  {formatDate(entry.recorded_at)}
                </p>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- Elderly */

function ElderlyPanel({
  data,
  medications,
  onChanged,
}: {
  data: any;
  medications: any[];
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function checkIn(wellbeing: string) {
    setBusy(true);
    try {
      const { data: response } = await api.post("/care/check-ins", {
        check_in_type: "elderly",
        wellbeing,
        responses: {},
      });
      setMessage(
        wellbeing === "need_help"
          ? "Your family contacts have been notified. Someone will check on you."
          : "Thank you. Your check-in has been recorded.",
      );
      if (response.guardian_alerts_raised > 0) {
        setMessage(
          `Recorded. ${response.guardian_alerts_raised} family contact(s) notified.`,
        );
      }
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function logDose(medicationId: string, status: string) {
    setBusy(true);
    try {
      await api.post("/care/medications/log", {
        medication_id: medicationId,
        status,
      });
      setMessage(
        status === "taken" ? "Marked as taken. Well done." : "Recorded.",
      );
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  // Elderly pathway: large buttons, high contrast, minimum steps (spec §14).

  return (
    <div className="space-y-4">
      <Card title="How are you feeling today?">
        {message && (
          <div className="sp-notice sp-notice-ok mb-4">
            <p>{message}</p>
          </div>
        )}
        <div className="grid sm:grid-cols-3 gap-3">
          {[
            { key: "good", label: "GOOD", icon: "circleCheck" as IconName, tone: "bg-ok-surface hover:bg-ok-border text-ok-text border-ok-border" },
            { key: "not_great", label: "NOT GREAT", icon: "warning" as IconName, tone: "bg-warn-surface hover:bg-warn-border text-warn-text border-warn-border" },
            { key: "need_help", label: "NEED HELP", icon: "emergency" as IconName, tone: "bg-danger-surface hover:bg-danger-border text-danger-text border-danger-border" },
          ].map((option) => (
            <button
              key={option.key}
              onClick={() => void checkIn(option.key)}
              disabled={busy}
              className={`sp-btn sp-btn-jumbo ${option.tone}`}
            >
              <Icon name={option.icon} size={30} />
              {option.label}
            </button>
          ))}
        </div>
        {data.checked_in_today && (
          <p className="text-sm text-ink-500 mt-3 text-center">
            You already checked in today ({data.todays_wellbeing?.replace(/_/g, " ")}).
          </p>
        )}
      </Card>

      {medications.length > 0 && (
        <Card title="Your medicines today">
          <div className="space-y-3">
            {medications.map((medication) => (
              <div
                key={medication.id}
                className="rounded-2xl border border-ink-200 p-4"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="font-bold text-ink-900 text-lg">
                      {medication.name} {medication.dosage}
                    </p>
                    <p className="text-ink-600">{medication.frequency_label}</p>
                  </div>
                  {medication.consecutive_missed >= 2 && (
                    <span className="sp-chip sp-chip-danger">
                      {medication.consecutive_missed} doses missed
                    </span>
                  )}
                </div>
                <div className="mt-3 grid grid-cols-3 gap-2">
                  {[
                    { key: "taken", label: "TAKEN", tone: "sp-btn-solid-ok" },
                    { key: "skipped", label: "SKIPPED", tone: "bg-ink-200 hover:bg-ink-300 text-ink-800" },
                    { key: "snoozed", label: "REMIND LATER", tone: "sp-btn-solid-warn" },
                  ].map((action) => (
                    <button
                      key={action.key}
                      onClick={() => void logDose(medication.id, action.key)}
                      disabled={busy}
                      className={`rounded-xl font-bold py-3.5 text-sm transition ${action.tone}`}
                    >
                      {action.label}
                    </button>
                  ))}
                </div>
                {medication.adherence_percent_14d != null && (
                  <p className="text-xs text-ink-500 mt-2">
                    {medication.adherence_percent_14d}% taken over the last 14 days
                  </p>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card title="Your care details">
        <div className="grid sm:grid-cols-2 gap-4 text-sm">
          <Detail label="Living situation" value={data.living_situation} />
          <Detail label="Mobility" value={data.mobility_level} />
          <Detail label="Fall risk" value={data.fall_risk_level} />
          <Detail
            label="Last check-in"
            value={data.last_check_in_at ? formatDate(data.last_check_in_at) : "—"}
          />
        </div>
        {data.consecutive_missed_checkins >= 2 && (
          <div className="sp-notice sp-notice-warn mt-4">
            <p>
              {data.consecutive_missed_checkins} check-ins missed. Your family
              contacts may have been notified.
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}

function Detail({ label, value }: { label: string; value?: string | null }) {
  return (
    <div>
      <p className="text-xs text-ink-500">{label}</p>
      <p className="font-medium text-ink-900 capitalize">
        {value?.replace(/_/g, " ") ?? "—"}
      </p>
    </div>
  );
}
