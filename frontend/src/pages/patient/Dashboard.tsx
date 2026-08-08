import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  AiNotice,
  Card,
  Confidence,
  Icon,
  type IconName,
  Empty,
  Spinner,
  StatusChip,
  UrgencyBadge,
  formatDateTime,
  relativeDay,
} from "../../components/ui";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { patientIllustration, programmeIllustration } from "../../lib/illustration";

const ACTIONS: {
  to: string;
  label: string;
  icon: IconName;
  primary?: boolean;
}[] = [
  { to: "/patient/symptom-check", label: "Start Symptom Check", icon: "chat", primary: true },
  { to: "/patient/find-care", label: "Find Doctor / Hospital", icon: "hospital" },
  { to: "/patient/reports", label: "Upload Medical Report", icon: "description" },
  { to: "/patient/imaging", label: "Medical Image Screening", icon: "scan" },
];

const PROGRAMME_STYLE: Record<string, string> = {
  maternal: "sp-gradient-maternal",
  postpartum: "sp-gradient-maternal",
  elderly: "sp-gradient-elderly",
  sexual_health: "sp-gradient-programme",
};

/**
 * One shape for every care-programme card.
 *
 * The three cards carry different content — two have artwork and a progress
 * bar, the confidential one has a shield and a link — and when each was built
 * inline they drifted into looking like different components. The media block
 * is a fixed height whether it holds an illustration or an icon, and the
 * footer is pinned to the bottom, so the cards line up across a row and stack
 * consistently on mobile regardless of what is inside them.
 */
function ProgrammeCard({
  to,
  href,
  accent,
  art,
  icon,
  title,
  subtitle,
  children,
}: {
  to?: string;
  href?: string;
  accent: string;
  art: string | null;
  icon?: IconName;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  const body = (
    <>
      <div className="mb-3 grid h-24 place-items-center">
        {art ? (
          <img
            src={art}
            alt=""
            aria-hidden="true"
            loading="lazy"
            className="pointer-events-none max-h-24 w-auto select-none object-contain"
          />
        ) : (
          icon && (
            <span className="grid h-16 w-16 place-items-center rounded-2xl bg-white/80 text-programme-text">
              <Icon name={icon} size={30} />
            </span>
          )
        )}
      </div>
      <p className="font-semibold text-ink-900">{title}</p>
      {subtitle && <p className="mt-1 text-sm text-ink-600">{subtitle}</p>}
      <div className="mt-auto pt-3">{children}</div>
    </>
  );

  const className = `flex h-full flex-col overflow-hidden rounded-lg border p-4 transition hover:shadow-md ${accent}`;

  return href ? (
    <a href={href} className={className}>
      {body}
    </a>
  ) : (
    <Link to={to!} className={className}>
      {body}
    </Link>
  );
}

export default function PatientDashboard() {
  const { user } = useAuth();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/patients/me/dashboard")
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;
  if (!data) return <Empty title="Could not load your dashboard." />;

  const recommendation = data.current_recommendation;

  return (
    <div className="space-y-6">
      {/* Welcome + primary action */}
      {/* Two independent columns rather than a row of grid cells. In a plain
          grid every card in a row waits for the tallest one, which left a band
          of empty space under the short hero next to the long recommendation.
          As columns each side flows at its own pace and the cards below move
          up into the space instead of starting beneath it. */}
      <div className="grid lg:grid-cols-3 gap-4 lg:gap-6 items-start">
        <div className="lg:col-span-2 space-y-4 lg:space-y-6">
        <div className="sp-card sp-gradient-brand-soft p-5 sm:p-6 flex gap-3 sm:gap-4 overflow-hidden">
          <div className="flex-1 min-w-0">
            <h1 className="sp-display text-2xl sm:text-[1.75rem]">
              Welcome back, {user?.full_name.split(" ")[0]}
            </h1>
            <p className="mt-1 text-ink-600">
              Let's take the next best step for your health.
            </p>
            <div className="mt-5 flex flex-wrap gap-2.5">
              {ACTIONS.map((action) => (
                <Link
                  key={action.to}
                  to={action.to}
                  className={action.primary ? "sp-btn sp-btn-primary" : "sp-btn sp-btn-secondary bg-white"}
                >
                  <Icon name={action.icon} size={18} />
                  {action.label}
                </Link>
              ))}
            </div>
          </div>

          {/* Decorative and gender-aware where the patient told us, neutral
              where they did not — see lib/illustration.ts. Hidden from
              assistive tech: it carries nothing the text does not. Shown on
              mobile too, just narrower — it is the one piece of the page that
              reflects the person using it. */}
          <img
            src={patientIllustration({
              sex: data.patient?.sex,
              age: data.patient?.age,
            })}
            alt=""
            aria-hidden="true"
            loading="lazy"
            className="pointer-events-none w-28 shrink-0 self-end select-none object-contain object-bottom sm:w-36 lg:w-48"
          />
        </div>

          {/* Appointments and reports sit inside the left column so they
              rise into the space beside the recommendation card rather
              than starting below it. */}
          <div className="grid sm:grid-cols-2 gap-4 lg:gap-6">
          {/* Appointments */}
          <Card
            title="Upcoming Appointments"
            action={
              <Link to="/patient/appointments" className="text-sm font-semibold text-brand-700 hover:underline">
                View all
              </Link>
            }
          >
            {data.upcoming_appointments?.length ? (
              <div className="space-y-3">
                {data.upcoming_appointments.map((appointment: any) => (
                  <div
                    key={appointment.id}
                    className="rounded-xl border border-ink-100 p-3.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="sp-chip bg-brand-100 text-brand-800">
                        {appointment.visit_type === "teleconsultation"
                          ? "Teleconsultation"
                          : "In-person visit"}
                      </span>
                      <StatusChip value={appointment.status} />
                    </div>
                    <div className="mt-2.5 flex items-baseline justify-between gap-2">
                      <p className="font-semibold text-ink-900">
                        {relativeDay(appointment.scheduled_start)}
                      </p>
                      <p className="text-sm text-ink-600">
                        {new Date(appointment.scheduled_start).toLocaleTimeString(
                          "en-GB",
                          { hour: "2-digit", minute: "2-digit" },
                        )}
                      </p>
                    </div>
                    <p className="text-sm text-ink-700 mt-1">
                      {appointment.doctor_name}
                    </p>
                    <p className="text-xs text-ink-500">
                      {appointment.specialty_name} · {appointment.hospital_name}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <Empty title="No upcoming appointments" />
            )}
          </Card>

          {/* Reports */}
          <Card
            title="Understand Your Medical Reports"
            action={
              <Link to="/patient/reports" className="text-sm font-semibold text-brand-700 hover:underline">
                View all
              </Link>
            }
          >
            {data.recent_report ? (
              <div className="space-y-3">
                <div className="flex items-start gap-3">
                  <span className="sp-icon-tile bg-brand-50 text-brand-700">
                    <Icon name="description" size={20} />
                  </span>
                  <div className="min-w-0">
                    <p className="font-medium text-ink-900 truncate">
                      {data.recent_report.file_name}
                    </p>
                    <p className="text-xs text-ink-500">
                      Uploaded {formatDateTime(data.recent_report.uploaded_at)}
                    </p>
                  </div>
                </div>
                <div className="rounded-xl bg-ink-50 p-3">
                  <p className="text-xs font-semibold text-ink-700">Summary</p>
                  <p className="text-sm text-ink-600 mt-0.5">
                    {data.recent_report.summary ?? "Processing…"}
                  </p>
                </div>
                <Link to="/patient/reports" className="sp-btn sp-btn-secondary sp-btn-sm w-full">
                  View explanation
                </Link>
              </div>
            ) : (
              <Empty
                title="No reports yet"
                hint="Upload a lab report to get a plain-language explanation."
              />
            )}
          </Card>
          </div>
        </div>

        <div className="space-y-4 lg:space-y-6">
        <Card
          title="Current Care Recommendation"
          action={
            recommendation && (
              <Link
                to="/patient/find-care"
                className="text-sm font-semibold text-brand-700 hover:underline"
              >
                View details
              </Link>
            )
          }
        >
          {recommendation ? (
            <div className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs text-ink-500">Likely specialty</p>
                  <p className="font-semibold text-ink-900">
                    {recommendation.specialty_name ?? recommendation.specialty_code}
                  </p>
                </div>
                <UrgencyBadge urgency={recommendation.urgency} />
              </div>
              <p className="text-sm text-ink-600">{recommendation.reason}</p>
              <div className="sp-notice sp-notice-warn flex-col !p-3">
                <p className="text-xs font-semibold">
                  Suggested next step
                </p>
                <p className="text-sm text-ink-700 mt-0.5">
                  {recommendation.suggested_next_action}
                </p>
              </div>
              <Confidence value={recommendation.confidence} />
              <AiNotice>
                AI-generated navigation support based on your reported
                information — not a diagnosis.
              </AiNotice>
            </div>
          ) : (
            <Empty
              title="No active recommendation"
              hint="Start a symptom check to get guidance."
            />
          )}
        </Card>
        {/* Screening */}
        <Card
          title="Image Screening"
          subtitle="For your information"
          action={
            <Link to="/patient/imaging" className="text-sm font-semibold text-brand-700 hover:underline">
              View all
            </Link>
          }
        >
          {data.recent_screening ? (
            <div className="space-y-3">
              <p className="text-xs text-ink-500">
                {data.recent_screening.modality.replace(/_/g, " ")} ·{" "}
                {formatDateTime(data.recent_screening.uploaded_at)}
              </p>
              <div className="rounded-xl bg-ink-50 p-3">
                <p className="text-xs font-semibold text-ink-700">
                  Possible finding
                </p>
                <p className="text-sm text-ink-900 font-medium mt-0.5">
                  {data.recent_screening.finding_label}
                </p>
              </div>
              <Confidence value={data.recent_screening.confidence} />
              <AiNotice>
                AI support only. Review with your doctor for accurate advice.
              </AiNotice>
            </div>
          ) : (
            <Empty title="No screenings yet" hint="Upload a supported medical image." />
          )}
        </Card>
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-4 lg:gap-6">
        {/* Care programmes */}
        <Card
          title="Care Programmes"
          className="lg:col-span-2"
          action={
            <Link to="/patient/programmes" className="text-sm font-semibold text-brand-700 hover:underline">
              View all
            </Link>
          }
        >
          {
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {!data.care_programmes?.length && (
                <div className="sm:col-span-2">
                  <Empty
                    title="Not enrolled in a care programme"
                    hint="Maternal and elderly pathways are available."
                  />
                </div>
              )}
              {data.care_programmes?.map((programme: any) => (
                <ProgrammeCard
                  key={programme.id}
                  to="/patient/programmes"
                  accent={PROGRAMME_STYLE[programme.type] ?? "sp-gradient-elderly"}
                  art={programmeIllustration(programme.type)}
                  title={programme.name}
                  subtitle={
                    data.maternal_summary && programme.type === "maternal"
                      ? `Pregnancy week ${data.maternal_summary.pregnancy_week}`
                      : "Reminders, check-ins and follow-up."
                  }
                >
                  <div className="h-1.5 rounded-full bg-white/70 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-brand-600"
                      style={{ width: `${programme.progress_percent}%` }}
                    />
                  </div>
                  <p className="text-xs text-ink-500 mt-1.5">
                    {Math.round(programme.progress_percent)}% complete
                  </p>
                </ProgrammeCard>
              ))}

              {/* Always offered, never "enrolled in": the confidential pathway
                  is reached anonymously rather than from this account, so it
                  is not part of care_programmes (spec §16). It carries a shield
                  rather than artwork — a recognisable sexual-health image is
                  exactly what someone glancing at a shared phone would notice —
                  but it keeps the same card shape so it does not read as a
                  different kind of thing. */}
              <ProgrammeCard
                href="/private"
                accent="sp-gradient-programme"
                art={null}
                icon="privacy"
                title="Confidential Sexual Health Support"
                subtitle="Private. Safe. Professional. We're here to help."
              >
                <span className="inline-flex items-center gap-1 text-sm font-semibold text-programme-text">
                  Continue privately
                  <Icon name="arrowRight" size={16} />
                </span>
              </ProgrammeCard>
            </div>
          }
        </Card>

        {/* Activity */}
        <Card title="Recent Activity">
          {data.recent_activity?.length ? (
            <ol className="space-y-3">
              {data.recent_activity.slice(0, 6).map((item: any, index: number) => (
                <li key={index} className="flex gap-3">
                  <span className="mt-1 h-2 w-2 rounded-full bg-brand-500 shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm text-ink-800 truncate">{item.title}</p>
                    <p className="text-xs text-ink-500">
                      {formatDateTime(item.at)}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <Empty title="No recent activity" />
          )}
        </Card>
      </div>

      {/* Medication reminders */}
      {data.medication_reminders?.length > 0 && (
        <Card title="Health Reminders">
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {data.medication_reminders.map((medication: any) => (
              <div
                key={medication.medication_id}
                className="rounded-xl border border-ink-100 p-3.5 flex items-center justify-between gap-3"
              >
                <div className="min-w-0">
                  <p className="font-medium text-ink-900 truncate">
                    {medication.name} {medication.dosage}
                  </p>
                  <p className="text-xs text-ink-500">
                    {medication.frequency_label}
                  </p>
                </div>
                {medication.is_critical && (
                  <span className="sp-chip sp-chip-warn">Critical</span>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
