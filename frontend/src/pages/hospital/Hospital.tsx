import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Card,
  Empty,
  Icon,
  Spinner,
  Stat,
  StatusChip,
  formatDateTime,
} from "../../components/ui";
import { ProposalInbox } from "../../components/Proposals";
import { api } from "../../lib/api";
import { chartColors } from "../../styles/theme";

// Chart colours resolve from the design tokens at runtime (see styles/theme.ts),
// so a token change flows through to every chart.
const TEAL = chartColors.brand;
const ORANGE = chartColors.warn;
const RED = chartColors.dangerSolid;
const NAVY = chartColors.ink;
const GRID = chartColors.grid;
const MUTED = chartColors.muted;
const AXIS_TICK = { fontSize: 11, fill: chartColors.ink } as const;

function titleCase(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/* ------------------------------------------------------------- Dashboard */

export function HospitalDashboard() {
  const [data, setData] = useState<any>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [demand, setDemand] = useState<any>(null);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [programmes, setProgrammes] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.get("/hospital/dashboard"),
      api.get("/hospital/appointments/demand-history", { params: { days: 30 } }),
      api.get("/hospital/forecast", { params: { horizon_days: 7 } }),
      api.get("/hospital/alerts"),
      api.get("/hospital/programmes"),
    ])
      .then(([dashboard, historyResponse, forecast, alertsResponse, programmesResponse]) => {
        setData(dashboard.data);
        setHistory(historyResponse.data);
        setDemand(forecast.data.by_specialty);
        setAlerts(alertsResponse.data);
        setProgrammes(programmesResponse.data);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner label="Computing operational analytics…" />;
  if (!data) return <Empty title="Could not load the dashboard." />;

  const kpis = data.kpis;
  const trendChart = history.map((point) => ({
    date: new Date(point.date).toLocaleDateString("en-GB", {
      day: "2-digit",
      month: "short",
    }),
    booked: point.booked,
  }));

  return (
    <div className="space-y-6">
      {/* What the models want done, as something you can approve — the
          dashboard below tells you a lot and asks for nothing. */}
      <ProposalInbox />

      <header>
        <h1 className="text-2xl font-bold text-ink-900">Admin Dashboard</h1>
        <p className="text-ink-500">
          {data.hospital.name} · Operations overview
        </p>
      </header>

      <div className="grid gap-3 sm:gap-4 grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <Stat label="Total intakes" value={kpis.total_intakes} icon="person" />
        <Stat
          label="Appointments booked"
          value={kpis.appointments_booked.toLocaleString()}
          hint={`vs ${kpis.appointments_booked_prev.toLocaleString()} last month`}
          icon="calendar"
        />
        <Stat
          label="No-show rate"
          value={`${kpis.no_show_rate}%`}
          tone={kpis.no_show_rate > 20 ? "danger" : "warn"}
          hint={`vs ${kpis.no_show_rate_prev}% last month`}
          icon="warning"
        />
        <Stat
          label="Referrals completed"
          value={kpis.referrals_completed.toLocaleString()}
          hint={`of ${kpis.referrals_total.toLocaleString()}`}
          tone="ok"
          icon="link"
        />
        <Stat
          label="Top specialty demand"
          value={
            kpis.top_specialty ? titleCase(kpis.top_specialty.code) : "—"
          }
          tone={kpis.top_specialty?.capacity_warning ? "danger" : "info"}
          hint={
            kpis.top_specialty?.capacity_warning ? "Capacity warning" : "Within capacity"
          }
          icon="stethoscope"
        />
        <Stat
          label="Avg intake to consult"
          value={
            kpis.avg_intake_to_consult_days != null
              ? `${kpis.avg_intake_to_consult_days} days`
              : "—"
          }
          icon="timer"
        />
      </div>

      {data.capacity_warnings?.length > 0 && (
        <div className="sp-notice sp-notice-warn flex-col items-stretch">
          <p className="font-bold">
            Predicted demand exceeds capacity in {data.capacity_warnings.length}{" "}
            specialty(ies)
          </p>
          {/* A data sentence, not a status label, so it wraps onto multiple
              lines on narrow screens rather than using the nowrap chip style. */}
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            {data.capacity_warnings.map((warning: any) => (
              <div
                key={warning.specialty_code}
                className="rounded-lg bg-surface border border-warn-border px-3 py-2 text-sm"
              >
                <span className="font-semibold">{titleCase(warning.specialty_code)}</span>
                {": "}
                {warning.predicted_total} predicted vs {warning.capacity_total}{" "}
                capacity ({warning.utilisation_percent}%)
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid lg:grid-cols-2 gap-4 lg:gap-6">
        <Card title="Appointment volume — last 30 days">
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={trendChart}>
              <defs>
                <linearGradient id="bookedFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={TEAL} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={TEAL} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
              <XAxis dataKey="date" tick={AXIS_TICK} interval={4} />
              <YAxis tick={AXIS_TICK} />
              <Tooltip />
              <Area
                isAnimationActive={false}
                type="monotone"
                dataKey="booked"
                stroke={TEAL}
                strokeWidth={2}
                fill="url(#bookedFill)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Specialty demand vs capacity — next 7 days">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={(demand?.specialties ?? []).slice(0, 7)}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
              <XAxis
                dataKey="specialty_code"
                tick={AXIS_TICK}
                tickFormatter={(value: string) => value.split("_")[0].slice(0, 8)}
              />
              <YAxis tick={AXIS_TICK} />
              <Tooltip
                formatter={(value: any, name: any) => [value, titleCase(String(name))]}
                labelFormatter={(label: any) => titleCase(String(label))}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar
                dataKey="capacity_total"
                name="Capacity"
                fill={MUTED}
                radius={[4, 4, 0, 0]}
                isAnimationActive={false}
              />
              {/* A base fill is required: Cell only overrides it per-datum, and
                  without it recharts renders the series with no fill at all. */}
              <Bar
                dataKey="predicted_total"
                name="Predicted"
                fill={TEAL}
                radius={[4, 4, 0, 0]}
                isAnimationActive={false}
              >
                {(demand?.specialties ?? []).slice(0, 7).map((entry: any, index: number) => (
                  <Cell key={index} fill={entry.capacity_warning ? RED : TEAL} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>

      <div className="grid lg:grid-cols-3 gap-4 lg:gap-6">
        <Card title="Operational alerts" className="lg:col-span-2">
          {alerts.length === 0 ? (
            <Empty title="No active alerts" />
          ) : (
            <div className="space-y-3">
              {alerts.map((alert, index) => (
                <div
                  key={index}
                  className="rounded-xl border border-ink-100 p-3.5 flex items-start gap-3"
                >
                  <Icon
                    name={alert.severity === "critical" ? "emergency" : "warning"}
                    size={20}
                    className={
                      alert.severity === "critical"
                        ? "text-danger-text"
                        : "text-warn-text"
                    }
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <p className="font-semibold text-ink-900">{alert.title}</p>
                      <StatusChip value={alert.severity} />
                    </div>
                    <p className="text-sm text-ink-600 mt-0.5">{alert.detail}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Programme enrolment">
          {programmes?.programmes?.length ? (
            <div className="space-y-3">
              <p className="text-3xl font-bold text-ink-900">
                {programmes.total.toLocaleString()}
              </p>
              <p className="text-sm text-ink-500 -mt-2">total active enrolments</p>
              {programmes.programmes.map((programme: any) => (
                <div key={programme.code}>
                  <div className="flex justify-between text-sm">
                    <span className="text-ink-700">{programme.name}</span>
                    <span className="font-semibold text-ink-900">
                      {programme.enrolled} ({programme.share_percent}%)
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 rounded-full bg-ink-100 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-programme-solid"
                      style={{ width: `${programme.share_percent}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="No enrolments" />
          )}
        </Card>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- Forecast */

export function Forecast() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/hospital/forecast", { params: { horizon_days: 7 } })
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner label="Generating forecast…" />;

  const specialties = data?.by_specialty?.specialties ?? [];

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Demand Forecast</h1>
        <p className="text-ink-500">
          Next 7 days, per specialty. Level + trend + day-of-week seasonality
          fitted on this facility's own booking history.
        </p>
      </header>

      {data?.by_specialty?.warnings?.length > 0 && (
        <div className="rounded-2xl border border-danger-border bg-danger-surface p-4">
          <p className="font-bold text-danger-text">Capacity warnings</p>
          <ul className="mt-2 space-y-1 text-sm text-ink-700">
            {data.by_specialty.warnings.map((warning: any) => (
              <li key={warning.specialty_code}>
                <strong>{titleCase(warning.specialty_code)}</strong>: predicted{" "}
                {warning.predicted_total} vs capacity {warning.capacity_total} —{" "}
                {warning.utilisation_percent}% utilisation
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid gap-4 lg:gap-5 lg:grid-cols-2">
        {specialties.slice(0, 8).map((specialty: any) => (
          <Card
            key={specialty.specialty_code}
            title={titleCase(specialty.specialty_code)}
            action={
              specialty.capacity_warning ? (
                <span className="sp-chip sp-chip-danger">Over capacity</span>
              ) : (
                <span className="sp-chip sp-chip-ok">
                  {specialty.utilisation_percent}% used
                </span>
              )
            }
          >
            <div className="grid grid-cols-3 gap-3 mb-4">
              <Metric label="Predicted" value={specialty.predicted_total} />
              <Metric label="Capacity" value={specialty.capacity_total} />
              <Metric label="Booked" value={specialty.booked_total} />
            </div>
            <ResponsiveContainer width="100%" height={160}>
              <AreaChart data={specialty.daily}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
                <XAxis dataKey="day_label" tick={AXIS_TICK} />
                <YAxis tick={AXIS_TICK} />
                <Tooltip />
                <Area
                  type="monotone"
                  dataKey="upper_bound"
                  stroke="none"
                  fill={TEAL}
                  fillOpacity={0.12}
                  name="Upper bound"
                />
                <Line
                  type="monotone"
                  dataKey="predicted_demand"
                  stroke={TEAL}
                  strokeWidth={2}
                  dot={false}
                  name="Predicted"
                />
                <Line
                  type="monotone"
                  dataKey="available_capacity"
                  stroke={NAVY}
                  strokeDasharray="4 4"
                  strokeWidth={1.5}
                  dot={false}
                  name="Capacity"
                />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        ))}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: any }) {
  return (
    <div className="rounded-xl bg-ink-50 p-3 text-center">
      <p className="text-xs text-ink-500">{label}</p>
      <p className="text-lg font-bold text-ink-900">{value}</p>
    </div>
  );
}

/* --------------------------------------------------------------- No-show */

/**
 * How good is the model?
 *
 * Every number on this page used to be a prediction with nothing behind it.
 * This card is the answer to "should I believe the risk percentages" — an
 * honest AUC and calibration on held-out data, or a plain statement that
 * there is not enough evidence yet.
 */
function ModelQuality() {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    api
      .get("/hospital/model-quality")
      .then((r) => setData(r.data))
      .catch(() => setData(null));
  }, []);

  if (!data?.backtest) return null;
  const b = data.backtest;
  if (b.status !== "ok") {
    return (
      <Card title="Model quality">
        <Empty
          title="Not enough resolved appointments yet"
          hint="Accuracy is measured once appointments have been completed or marked missed."
        />
      </Card>
    );
  }

  const pct = (v: number | null) => (v == null ? "—" : v.toFixed(3));
  return (
    <Card
      title="Model quality"
      subtitle="Measured on held-out appointments the model never trained on."
    >
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="AUC" value={pct(b.auc)} hint="1.0 is perfect ranking, 0.5 is chance" />
        <Stat label="Brier" value={pct(b.brier)} hint="lower is better" />
        <Stat label="Calibration error" value={pct(b.ece)} hint="predicted vs observed" />
        <Stat label="Tested on" value={String(b.n ?? 0)} hint="appointments" />
      </div>

      {Array.isArray(b.calibration) && b.calibration.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-semibold text-ink-700 mb-1.5">
            When it says X, how often does it happen?
          </p>
          <div className="space-y-1">
            {b.calibration.map((row: any) => (
              <div key={row.lower} className="flex items-center gap-2 text-sm">
                <span className="w-24 text-ink-500">
                  says {(row.predicted * 100).toFixed(0)}%
                </span>
                <span className="text-ink-400">→</span>
                <span className="font-medium text-ink-900">
                  {(row.observed * 100).toFixed(0)}% missed
                </span>
                <span className="text-xs text-ink-400">({row.n})</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.deployed?.status === "no_resolved_predictions" && (
        <p className="mt-4 text-xs text-ink-500">
          Live predictions are not graded yet — an appointment has to be
          completed or marked missed by a person first. Statuses closed
          automatically when a slot elapsed are excluded, because they record
          that nobody updated the appointment rather than that the patient
          did not attend.
        </p>
      )}
    </Card>
  );
}

export function NoShow() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/hospital/no-show")
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner label="Scoring upcoming appointments…" />;
  if (!data) return <Empty title="Could not load predictions." />;

  const bands = [
    { name: "High risk", value: data.by_risk_band.high, fill: RED },
    { name: "Medium risk", value: data.by_risk_band.medium, fill: ORANGE },
    { name: "Low risk", value: data.by_risk_band.low, fill: TEAL },
  ];

  return (
    <div className="space-y-5">
      <ModelQuality />

      <header>
        <h1 className="text-2xl font-bold text-ink-900">No-show Risk</h1>
        <p className="text-ink-500">
          Logistic model fitted on this facility's resolved appointment history.
          Bands are relative to the observed no-show rate.
        </p>
      </header>

      <div className="grid gap-3 sm:gap-4 grid-cols-2 sm:grid-cols-4">
        <Stat label="Upcoming appointments" value={data.total_upcoming} icon="calendar" />
        <Stat
          label="High risk"
          value={data.by_risk_band.high}
          tone="danger"
          icon="error"
        />
        <Stat
          label="Medium risk"
          value={data.by_risk_band.medium}
          tone="warn"
          icon="warning"
        />
        <Stat
          label="Historical no-show rate"
          value={`${Math.round(data.historical_no_show_rate * 100)}%`}
          icon="trend"
        />
      </div>

      {data.tomorrow_high_risk_count > 0 && (
        <div className="sp-notice sp-notice-warn flex-col">
          <p className="font-bold">
            Tomorrow: {data.tomorrow_high_risk_count} high-risk appointments
          </p>
          <p className="text-sm text-ink-700 mt-1">
            Consider reminder calls or releasing capacity for waiting patients.
          </p>
        </div>
      )}

      <div className="grid lg:grid-cols-3 gap-4 lg:gap-6">
        <Card title="Risk distribution">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={bands} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke={GRID} horizontal={false} />
              <XAxis type="number" tick={AXIS_TICK} />
              <YAxis
                type="category"
                dataKey="name"
                width={90}
                tick={AXIS_TICK}
              />
              <Tooltip />
              <Bar dataKey="value" radius={[0, 6, 6, 0]}>
                {bands.map((band, index) => (
                  <Cell key={index} fill={band.fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Highest-risk upcoming appointments" className="lg:col-span-2">
          {data.top_risk?.length === 0 ? (
            <Empty title="No upcoming appointments to score" />
          ) : (
            <div className="sp-table-wrap -mx-4 px-4 sm:-mx-5 sm:px-5">
              <table className="w-full text-sm min-w-[560px]">
                <thead>
                  <tr className="text-left text-ink-500 border-b border-ink-100">
                    <th className="pb-2 font-medium">Patient</th>
                    <th className="pb-2 font-medium">When</th>
                    <th className="pb-2 font-medium">Risk</th>
                    <th className="pb-2 font-medium">Why</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-50">
                  {data.top_risk.slice(0, 12).map((row: any) => (
                    <tr key={row.appointment_id}>
                      <td className="py-2.5 font-medium text-ink-900">
                        {row.patient_name}
                      </td>
                      <td className="py-2.5 text-ink-600">
                        {formatDateTime(row.scheduled_start)}
                      </td>
                      <td className="py-2.5">
                        <div className="flex items-center gap-2">
                          <StatusChip value={row.risk_band} />
                          <span className="text-xs text-ink-500">
                            {Math.round(row.probability * 100)}%
                          </span>
                        </div>
                      </td>
                      <td className="py-2.5">
                        <div className="flex flex-wrap gap-1">
                          {row.contributing_factors.slice(0, 2).map((factor: any) => (
                            <span
                              key={factor.feature}
                              className="sp-chip bg-ink-100 text-ink-700"
                            >
                              {factor.label}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- Capacity */

export function Capacity() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/hospital/capacity")
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Facility Capacity</h1>
        <p className="text-ink-500">{data?.facility?.name}</p>
      </header>

      {data?.facility && (
        <div className="grid gap-3 sm:gap-4 grid-cols-2 sm:grid-cols-3">
          <Stat label="Beds" value={data.facility.bed_count} icon="bed" />
          <Stat label="ICU beds" value={data.facility.icu_bed_count} tone="danger" icon="hospital" />
          <Stat
            label="Consultation rooms"
            value={data.facility.consultation_rooms}
            icon="door"
          />
        </div>
      )}

      <Card title="Utilisation by specialty — next 7 days">
        <div className="space-y-3">
          {data?.specialties?.map((specialty: any) => (
            <div key={specialty.specialty_code}>
              <div className="flex items-center justify-between text-sm mb-1">
                <span className="font-medium text-ink-800">
                  {specialty.specialty_name}
                  <span className="text-ink-400 font-normal">
                    {" "}· {specialty.doctor_count} doctor(s)
                  </span>
                </span>
                <span
                  className={`font-semibold ${
                    specialty.capacity_warning ? "text-danger-text" : "text-ink-700"
                  }`}
                >
                  {specialty.booked} / {specialty.weekly_capacity} (
                  {specialty.utilisation_percent}%)
                </span>
              </div>
              <div className="h-2.5 rounded-full bg-ink-100 overflow-hidden">
                <div
                  className={`h-full rounded-full ${
                    specialty.utilisation_percent > 90
                      ? "bg-danger-solid"
                      : specialty.utilisation_percent > 70
                        ? "bg-warn-solid"
                        : "bg-brand-500"
                  }`}
                  style={{
                    width: `${Math.min(specialty.utilisation_percent, 100)}%`,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------- Providers */

export function Providers() {
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/hospital/doctors")
      .then((r) => setItems(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Provider Roster</h1>
        <p className="text-ink-500">{items.length} active providers</p>
      </header>

      <Card className="!p-0 overflow-hidden">
        <div className="sp-table-wrap">
          <table className="w-full text-sm min-w-[720px]">
            <thead>
              <tr className="text-left text-ink-500 border-b border-ink-100">
                <th className="py-2.5 px-4 font-medium">Provider</th>
                <th className="py-2.5 px-4 font-medium">Specialty</th>
                <th className="py-2.5 px-4 font-medium">Experience</th>
                <th className="py-2.5 px-4 font-medium">This week</th>
                <th className="py-2.5 px-4 font-medium">Availability</th>
                <th className="py-2.5 px-4 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-50">
              {items.map((doctor) => (
                <tr key={doctor.doctor_id} className="hover:bg-ink-50">
                  <td className="py-3 px-4 font-medium text-ink-900">
                    {doctor.name}
                  </td>
                  <td className="py-3 px-4 text-ink-600">
                    {doctor.specialty_name}
                    {doctor.sub_specialty && (
                      <span className="block text-xs text-ink-400">
                        {doctor.sub_specialty}
                      </span>
                    )}
                  </td>
                  <td className="py-3 px-4 text-ink-600">
                    {doctor.years_experience} yrs
                  </td>
                  <td className="py-3 px-4 text-ink-700">
                    {doctor.weekly_booked} / {doctor.weekly_capacity}
                  </td>
                  <td className="py-3 px-4">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-16 rounded-full bg-ink-100 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-brand-500"
                          style={{ width: `${doctor.availability_percent}%` }}
                        />
                      </div>
                      <span className="text-xs text-ink-600">
                        {doctor.availability_percent}%
                      </span>
                    </div>
                  </td>
                  <td className="py-3 px-4">
                    <StatusChip
                      value={
                        doctor.verification_status === "verified"
                          ? "completed"
                          : "pending"
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
