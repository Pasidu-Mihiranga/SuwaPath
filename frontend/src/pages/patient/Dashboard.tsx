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
import { ProposalInbox } from "../../components/Proposals";
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
 * Clean card for each care programme.
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
      <div className="mb-3 grid h-20 place-items-center">
        {art ? (
          <img
            src={art}
            alt=""
            aria-hidden="true"
            loading="lazy"
            className="pointer-events-none max-h-20 w-auto select-none object-contain"
          />
        ) : (
          icon && (
            <span className="grid h-14 w-14 place-items-center rounded-2xl bg-white/80 text-programme-text shadow-sm">
              <Icon name={icon} size={26} />
            </span>
          )
        )}
      </div>
      <p className="font-semibold text-ink-900 break-words text-sm sm:text-base">{title}</p>
      {subtitle && <p className="mt-0.5 text-xs sm:text-sm text-ink-600 break-words">{subtitle}</p>}
      <div className="mt-auto pt-3 w-full min-w-0">{children}</div>
    </>
  );

  const className = `flex h-full flex-col justify-between overflow-hidden rounded-xl border p-4 sm:p-5 transition-all duration-200 hover:shadow-md min-w-0 w-full ${accent}`;

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
    <div className="space-y-6 w-full min-w-0 max-w-full pb-6">
      {/* ── Main Responsive Grid ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 lg:gap-6 items-start w-full min-w-0">
        
        {/* ◀ LEFT COLUMN (2/3 width on desktop) */}
        <div className="lg:col-span-2 space-y-5 lg:space-y-6 w-full min-w-0">
          
          {/* 1. Welcome & Primary Actions Card (Top Hero) */}
          <div className="sp-card sp-gradient-brand-soft p-5 sm:p-6 overflow-hidden shadow-sm border border-brand-200/60">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <h1 className="sp-display text-xl sm:text-2xl lg:text-[1.75rem] text-ink-900 tracking-tight">
                  Welcome back, {user?.full_name.split(" ")[0]}
                </h1>
                <p className="mt-1 text-xs sm:text-sm text-ink-600">
                  Let's take the next best step for your health.
                </p>
              </div>
              <img
                src={patientIllustration({
                  sex: data.patient?.sex,
                  age: data.patient?.age,
                })}
                alt=""
                aria-hidden="true"
                loading="lazy"
                className="pointer-events-none h-16 w-16 sm:h-20 sm:w-20 shrink-0 select-none object-contain"
              />
            </div>

            <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {ACTIONS.map((action) => (
                <Link
                  key={action.to}
                  to={action.to}
                  className={`sp-btn sp-btn-block justify-start text-xs sm:text-sm !py-2.5 font-medium transition-all ${
                    action.primary
                      ? "sp-btn-primary shadow-sm"
                      : "sp-btn-secondary bg-white hover:bg-ink-50"
                  }`}
                >
                  <Icon name={action.icon} size={17} className="shrink-0" />
                  <span className="truncate">{action.label}</span>
                </Link>
              ))}
            </div>
          </div>

          {/* 2. Compact & Slim Health Reminders (Positioned at Top) */}
          {data.medication_reminders?.length > 0 && (
            <div className="sp-card p-3.5 sm:p-4 bg-white border border-ink-100 shadow-sm rounded-xl">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 pb-2 border-b border-ink-100/60">
                <div className="flex items-center gap-2">
                  <span className="sp-icon-tile bg-brand-50 text-brand-700 !h-7 !w-7 shrink-0">
                    <Icon name="medication" size={15} />
                  </span>
                  <p className="font-semibold text-xs uppercase tracking-wider text-ink-800">
                    Health Reminders
                  </p>
                  <span className="sp-chip sp-chip-neutral text-[10px] !py-0 !px-1.5 font-medium">
                    {data.medication_reminders.length} active
                  </span>
                </div>
                <Link
                  to="/patient/records"
                  className="text-xs font-semibold text-brand-700 hover:underline inline-flex items-center gap-1 self-end sm:self-auto"
                >
                  <span>View prescriptions</span>
                  <Icon name="arrowRight" size={12} />
                </Link>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 pt-2.5">
                {data.medication_reminders.slice(0, 4).map((medication: any) => (
                  <div
                    key={medication.medication_id}
                    className="rounded-lg border border-ink-100 p-2.5 flex items-center justify-between gap-2 bg-ink-50/40 hover:bg-ink-50 transition-colors"
                  >
                    <div className="min-w-0">
                      <p className="font-semibold text-xs text-ink-900 truncate">
                        {medication.name} {medication.dosage}
                      </p>
                      <p className="text-[11px] text-ink-500 truncate mt-0.5">
                        {medication.frequency_label}
                      </p>
                    </div>
                    {medication.is_critical && (
                      <span className="sp-chip sp-chip-warn text-[10px] !py-0.5 !px-1.5 shrink-0">
                        Critical
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 3. Sub-grid: Upcoming Appointments & Medical Reports */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5 lg:gap-6 w-full min-w-0">
            
            {/* Appointments */}
            <Card
              title="Upcoming Appointments"
              icon="calendar"
              action={
                <Link
                  to="/patient/appointments"
                  className="text-xs font-semibold text-brand-700 hover:underline inline-flex items-center gap-1"
                >
                  View all
                  <Icon name="arrowRight" size={13} />
                </Link>
              }
            >
              {data.upcoming_appointments?.length ? (
                <div className="space-y-3">
                  {data.upcoming_appointments.map((appointment: any) => (
                    <div
                      key={appointment.id}
                      className="rounded-xl border border-ink-100 p-3.5 bg-white hover:border-brand-200 transition-colors"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="sp-chip bg-brand-50 text-brand-800 text-xs">
                          {appointment.visit_type === "teleconsultation"
                            ? "Teleconsultation"
                            : "In-person visit"}
                        </span>
                        <StatusChip value={appointment.status} />
                      </div>
                      <div className="mt-2.5 flex items-baseline justify-between gap-2">
                        <p className="font-semibold text-ink-900 text-sm sm:text-base">
                          {relativeDay(appointment.scheduled_start)}
                        </p>
                        <p className="text-xs sm:text-sm font-medium text-ink-600">
                          {new Date(appointment.scheduled_start).toLocaleTimeString(
                            "en-GB",
                            { hour: "2-digit", minute: "2-digit" },
                          )}
                        </p>
                      </div>
                      <p className="text-xs sm:text-sm font-medium text-ink-800 mt-1 truncate">
                        {appointment.doctor_name}
                      </p>
                      <p className="text-xs text-ink-500 mt-0.5 truncate">
                        {appointment.specialty_name} · {appointment.hospital_name}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <Empty
                  title="No upcoming appointments"
                  hint="Schedule a consultation with a specialist."
                  icon="calendar"
                />
              )}
            </Card>

            {/* Medical Reports */}
            <Card
              title="Understand Your Medical Reports"
              icon="description"
              action={
                <Link
                  to="/patient/reports"
                  className="text-xs font-semibold text-brand-700 hover:underline inline-flex items-center gap-1"
                >
                  View all
                  <Icon name="arrowRight" size={13} />
                </Link>
              }
            >
              {data.recent_report ? (
                <div className="space-y-3 flex-1 flex flex-col justify-between">
                  <div>
                    <div className="flex items-start gap-2.5">
                      <span className="sp-icon-tile bg-brand-50 text-brand-700 !h-8 !w-8 shrink-0">
                        <Icon name="description" size={17} />
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="font-semibold text-sm text-ink-900 truncate">
                          {data.recent_report.file_name}
                        </p>
                        <p className="text-[11px] text-ink-500">
                          Uploaded {formatDateTime(data.recent_report.uploaded_at)}
                        </p>
                      </div>
                    </div>
                    
                    <div className="rounded-xl bg-ink-50 p-3 mt-2.5 border border-ink-100/60">
                      <p className="text-xs font-semibold text-ink-700">Summary</p>
                      <p className="text-xs text-ink-600 mt-1 line-clamp-3 leading-relaxed">
                        {data.recent_report.summary ?? "Processing report data…"}
                      </p>
                    </div>
                  </div>

                  <div className="pt-2">
                    <Link
                      to="/patient/reports"
                      className="sp-btn sp-btn-secondary sp-btn-sm w-full justify-center"
                    >
                      <span>View explanation</span>
                    </Link>
                  </div>
                </div>
              ) : (
                <Empty
                  title="No reports yet"
                  hint="Upload a lab report to get a plain-language explanation."
                  icon="description"
                />
              )}
            </Card>
          </div>

          {/* 4. Care Programmes */}
          <Card
            title="Care Programmes"
            subtitle="Targeted healthcare tracking"
            action={
              <Link
                to="/patient/programmes"
                className="text-xs font-semibold text-brand-700 hover:underline inline-flex items-center gap-1"
              >
                View all
                <Icon name="arrowRight" size={13} />
              </Link>
            }
          >
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5 w-full min-w-0">
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
                  <p className="text-xs text-ink-600 mt-1.5 font-medium">
                    {Math.round(programme.progress_percent)}% complete
                  </p>
                </ProgrammeCard>
              ))}

              {/* Confidential Support Shortcut */}
              <ProgrammeCard
                href="/private"
                accent="sp-gradient-programme"
                art={null}
                icon="privacy"
                title="Confidential Sexual Health Support"
                subtitle="Private. Safe. Professional. We're here to help."
              >
                <span className="inline-flex items-center gap-1 text-xs sm:text-sm font-semibold text-programme-text">
                  Continue privately
                  <Icon name="arrowRight" size={14} />
                </span>
              </ProgrammeCard>
            </div>
          </Card>
        </div>

        {/* ▶ RIGHT COLUMN (1/3 width on desktop) */}
        <div className="space-y-5 lg:space-y-6 w-full min-w-0">
          
          {/* Current Care Recommendation */}
          <Card
            title="Current Care Recommendation"
            icon="warning"
            action={
              recommendation && (
                <Link
                  to="/patient/find-care"
                  className="text-xs font-semibold text-brand-700 hover:underline"
                >
                  View details
                </Link>
              )
            }
          >
            {recommendation ? (
              <div className="space-y-3.5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs text-ink-500 font-medium">Likely specialty</p>
                    <p className="font-bold text-ink-900 text-base">
                      {recommendation.specialty_name ?? recommendation.specialty_code}
                    </p>
                  </div>
                  <UrgencyBadge urgency={recommendation.urgency} />
                </div>
                
                <p className="text-xs sm:text-sm text-ink-600 leading-relaxed">
                  {recommendation.reason}
                </p>
                
                <div className="sp-notice sp-notice-warn flex-col !p-3 rounded-xl">
                  <p className="text-xs font-semibold text-warn-text">
                    Suggested next step
                  </p>
                  <p className="text-xs sm:text-sm text-ink-800 mt-0.5 leading-snug">
                    {recommendation.suggested_next_action}
                  </p>
                </div>
                
                <div className="pt-1 flex items-center justify-between">
                  <Confidence value={recommendation.confidence} />
                </div>
                
                <AiNotice>
                  AI-generated navigation support based on your reported information — not a diagnosis.
                </AiNotice>
              </div>
            ) : (
              <Empty
                title="No active recommendation"
                hint="Start a symptom check to get tailored care guidance."
              />
            )}
          </Card>

          {/* Image Screening */}
          <Card
            title="Image Screening"
            subtitle="For your information"
            icon="scan"
            action={
              <Link
                to="/patient/imaging"
                className="text-xs font-semibold text-brand-700 hover:underline"
              >
                View all
              </Link>
            }
          >
            {data.recent_screening ? (
              <div className="space-y-3">
                <div className="flex items-center justify-between text-xs text-ink-500">
                  <span className="capitalize font-medium text-ink-700">
                    {data.recent_screening.modality.replace(/_/g, " ")}
                  </span>
                  <span>{formatDateTime(data.recent_screening.uploaded_at)}</span>
                </div>
                
                <div className="rounded-xl bg-ink-50 p-3 border border-ink-100/60">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-600">
                    Possible finding
                  </p>
                  <p className="text-sm text-ink-900 font-semibold mt-0.5">
                    {data.recent_screening.finding_label}
                  </p>
                </div>
                
                <div className="pt-1">
                  <Confidence value={data.recent_screening.confidence} />
                </div>
                
                <AiNotice>
                  AI support only. Review with your doctor for accurate advice.
                </AiNotice>
              </div>
            ) : (
              <Empty title="No screenings yet" hint="Upload a supported medical scan." />
            )}
          </Card>

          {/* Recent Activity (Styled Timeline) */}
          <Card
            title="Recent Activity"
            icon="history"
          >
            {data.recent_activity?.length ? (
              <ol className="space-y-3 relative before:absolute before:top-2 before:bottom-2 before:left-1 before:w-0.5 before:bg-ink-100">
                {data.recent_activity.slice(0, 5).map((item: any, index: number) => (
                  <li key={index} className="flex items-start gap-3 pl-0.5 relative">
                    <span className="mt-1 h-2 w-2 rounded-full bg-brand-500 ring-4 ring-white shrink-0 z-10" />
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-medium text-ink-800 truncate">{item.title}</p>
                      <p className="text-[11px] text-ink-400 mt-0.5">
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
      </div>

      {/* ── 5. Suggestions / Action Proposals (Positioned Below Main Grid) ── */}
      <div className="pt-2">
        <ProposalInbox />
      </div>
    </div>
  );
}
