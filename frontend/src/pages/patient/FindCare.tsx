import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Chip,
  Empty,
  Icon,
  ErrorNote,
  Spinner,
  UrgencyBadge,
  formatDate,
} from "../../components/ui";
import { api, errorMessage } from "../../lib/api";

type Tab = "doctors" | "hospitals" | "diagnostic-centres";

const TABS: { key: Tab; label: string }[] = [
  { key: "doctors", label: "Doctors" },
  { key: "hospitals", label: "Hospitals" },
  { key: "diagnostic-centres", label: "Diagnostic Centres" },
];

export default function FindCare() {
  const [params] = useSearchParams();
  const recommendationId = params.get("recommendation") ?? undefined;
  // Free text from the top bar. There is no server-side text search on
  // /providers, so this narrows the matches already returned rather than
  // pretending to search the whole directory.
  const query = (params.get("q") ?? "").trim().toLowerCase();

  const [tab, setTab] = useState<Tab>("doctors");
  const [specialties, setSpecialties] = useState<any[]>([]);
  const [specialty, setSpecialty] = useState<string>("");
  const [visitType, setVisitType] = useState<string>("");
  const [maxDistance, setMaxDistance] = useState<string>("");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [booking, setBooking] = useState<any>(null);

  useEffect(() => {
    api.get("/providers/specialties").then((r) => setSpecialties(r.data));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get(`/providers/${tab}`, {
        params: {
          recommendation_id: recommendationId,
          specialty_code: specialty || undefined,
          visit_type: tab === "doctors" && visitType ? visitType : undefined,
          max_distance_km: maxDistance || undefined,
          limit: 12,
        },
      });
      setData(data);
    } catch (err) {
      setError(errorMessage(err, "Could not load results."));
    } finally {
      setLoading(false);
    }
  }, [tab, recommendationId, specialty, visitType, maxDistance]);

  useEffect(() => {
    void load();
  }, [load]);

  const visibleResults: any[] = !query
    ? data?.results ?? []
    : (data?.results ?? []).filter((item: any) =>
        [item.name, item.specialty_name, item.hospital_name, item.city]
          .filter(Boolean)
          .some((field: string) => field.toLowerCase().includes(query)),
      );

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Find Care</h1>
        <p className="text-ink-500">
          Matched on specialty, availability, distance, and whether the facility can run the tests you need.
        </p>
      </header>

      {data?.recommendation_reason && (
        <div className="sp-card p-4 border-l-4 border-l-brand-500">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold text-brand-800">
                Based on your recommendation
              </p>
              <p className="text-sm text-ink-700 mt-1">
                {data.recommendation_reason}
              </p>
            </div>
            <UrgencyBadge urgency={data.criteria?.urgency} />
          </div>
          {data.criteria?.required_capabilities?.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              <span className="text-xs text-ink-500 mr-1">
                Facility must provide:
              </span>
              {data.criteria.required_capabilities.map((capability: string) => (
                <span key={capability} className="sp-chip bg-brand-100 text-brand-800">
                  {capability.replace(/_/g, " ")}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tabs + filters */}
      <div className="sp-card p-4 space-y-3">
        <div className="flex gap-1 border-b border-ink-100 -mx-4 px-4 -mt-4 pt-1 overflow-x-auto no-scrollbar">
          {TABS.map((entry) => (
            <button
              key={entry.key}
              onClick={() => setTab(entry.key)}
              className={`px-3 sm:px-4 py-2.5 text-xs sm:text-sm font-semibold border-b-2 whitespace-nowrap transition ${
                tab === entry.key
                  ? "border-brand-600 text-brand-700"
                  : "border-transparent text-ink-500 hover:text-ink-800"
              }`}
            >
              {entry.label}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 w-full min-w-0">
          <select
            className="sp-select w-full py-2 min-w-0"
            value={specialty}
            onChange={(event) => setSpecialty(event.target.value)}
          >
            <option value="">
              {recommendationId ? "Recommended specialty" : "All specialties"}
            </option>
            {specialties.map((entry) => (
              <option key={entry.code} value={entry.code}>
                {entry.name}
              </option>
            ))}
          </select>

          {tab === "doctors" && (
            <select
              className="sp-select w-full py-2 min-w-0"
              value={visitType}
              onChange={(event) => setVisitType(event.target.value)}
            >
              <option value="">Any consultation type</option>
              <option value="physical">In-person</option>
              <option value="teleconsultation">Teleconsultation</option>
            </select>
          )}

          <select
            className="sp-select w-full py-2 min-w-0"
            value={maxDistance}
            onChange={(event) => setMaxDistance(event.target.value)}
          >
            <option value="">Any distance</option>
            <option value="5">Within 5 km</option>
            <option value="15">Within 15 km</option>
            <option value="40">Within 40 km</option>
          </select>
        </div>
      </div>

      <ErrorNote message={error} />

      {loading ? (
        <Spinner label="Matching providers…" />
      ) : !visibleResults.length ? (
        <Empty
          title="No matches found"
          hint={
            query
              ? `Nothing here matches “${query}”. Try widening your filters.`
              : "Try widening your filters."
          }
        />
      ) : (
        <div className="grid gap-3 sm:gap-4 lg:grid-cols-2">
          {visibleResults.map((item: any) =>
            tab === "doctors" ? (
              <DoctorCard
                key={item.doctor_id}
                doctor={item}
                onBook={() => setBooking(item)}
              />
            ) : (
              <FacilityCard key={item.hospital_id} facility={item} />
            ),
          )}
        </div>
      )}

      {booking && (
        <BookingModal
          doctor={booking}
          recommendationId={recommendationId}
          onClose={() => setBooking(null)}
          onBooked={() => {
            setBooking(null);
            void load();
          }}
        />
      )}
    </div>
  );
}

function DoctorCard({ doctor, onBook }: { doctor: any; onBook: () => void }) {
  return (
    <div className="sp-card p-5">
      <div className="flex items-start gap-4">
        <div className="h-14 w-14 rounded-2xl bg-brand-100 text-brand-800 grid place-items-center font-bold text-lg shrink-0">
          {doctor.name.replace("Dr. ", "").split(" ").map((p: string) => p[0]).slice(0, 2).join("")}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="font-bold text-ink-900">{doctor.name}</h3>
            {doctor.verification_status === "verified" && (
              <Chip tone="info" icon="verified">Verified</Chip>
            )}
          </div>
          <p className="text-sm text-ink-600">
            {doctor.specialty_name}
            {doctor.sub_specialty ? ` · ${doctor.sub_specialty}` : ""}
          </p>
          <p className="text-sm text-ink-500">
            {doctor.years_experience}+ yrs exp · {doctor.hospital_name}
          </p>
        </div>
        <div className="text-right shrink-0">
          <p className="text-sm font-bold text-ink-900">
            LKR {doctor.consultation_fee_lkr?.toLocaleString()}
          </p>
          {doctor.distance_km != null && (
            <p className="text-xs text-ink-500">{doctor.distance_km} km away</p>
          )}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {doctor.languages?.map((language: string) => (
          <span key={language} className="sp-chip bg-ink-100 text-ink-700">
            {{ en: "English", si: "සිංහල", ta: "தமிழ்" }[language] ?? language}
          </span>
        ))}
        {doctor.supports_teleconsultation && (
          <Chip tone="programme">Teleconsultation</Chip>
        )}
      </div>

      {doctor.matched_capabilities?.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {doctor.matched_capabilities.map((capability: string) => (
            <Chip key={capability} tone="ok" icon="check">
              {capability}
            </Chip>
          ))}
        </div>
      )}
      {doctor.missing_capabilities?.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {doctor.missing_capabilities.map((capability: string) => (
            <Chip key={capability} tone="warn" icon="close">
              {capability} not on site
            </Chip>
          ))}
        </div>
      )}

      {/* Why this doctor was recommended (spec §7) */}
      <p className="mt-3 text-sm text-ink-600 bg-ink-50 rounded-xl p-3">
        {doctor.explanation}
      </p>

      <div className="mt-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs text-ink-500">Next available</p>
          <p className="text-sm font-semibold text-ink-900">
            {doctor.next_available
              ? `${doctor.next_available.date_label}, ${doctor.next_available.label}`
              : "No slots in the next 3 weeks"}
          </p>
        </div>
        <button
          className="sp-btn sp-btn-primary sp-btn-sm"
          onClick={onBook}
          disabled={!doctor.next_available}
        >
          Book appointment
        </button>
      </div>
    </div>
  );
}

function FacilityCard({ facility }: { facility: any }) {
  return (
    <div className="sp-card p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-bold text-ink-900">{facility.name}</h3>
          <p className="text-sm text-ink-500">
            {facility.city} · {facility.distance_km} km away
          </p>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          {facility.has_emergency && (
            <Chip tone="danger">24h Emergency</Chip>
          )}
          {facility.is_24_hours && (
            <span className="sp-chip bg-brand-100 text-brand-800">Open 24 hours</span>
          )}
        </div>
      </div>

      <p className="mt-3 text-sm text-ink-600 bg-ink-50 rounded-xl p-3">
        {facility.explanation}
      </p>

      {facility.matched_capabilities?.length > 0 && (
        <div className="mt-3">
          <p className="text-xs text-ink-500 mb-1.5">Provides what you need</p>
          <div className="flex flex-wrap gap-1.5">
            {facility.matched_capabilities.map((capability: string) => (
              <Chip key={capability} tone="ok" icon="check">
                {capability}
              </Chip>
            ))}
          </div>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between text-sm">
        <span className="text-ink-500">
          {facility.available_doctor_count > 0
            ? `${facility.available_doctor_count} matching specialist(s)`
            : `${facility.all_capabilities?.length ?? 0} services`}
        </span>
        <a
          className="inline-flex items-center gap-1 text-brand-700 font-semibold hover:underline"
          href={`https://www.google.com/maps/search/?api=1&query=${facility.latitude},${facility.longitude}`}
          target="_blank"
          rel="noreferrer"
        >
          View on map
          <Icon name="arrowRight" size={16} />
        </a>
      </div>
    </div>
  );
}

function BookingModal({
  doctor,
  recommendationId,
  onClose,
  onBooked,
}: {
  doctor: any;
  recommendationId?: string;
  onClose: () => void;
  onBooked: () => void;
}) {
  const [days, setDays] = useState<any[]>([]);
  const [selectedDay, setSelectedDay] = useState<string>("");
  const [slot, setSlot] = useState<any>(null);
  const [visitType, setVisitType] = useState("physical");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<any>(null);

  useEffect(() => {
    api
      .get(`/providers/doctors/${doctor.doctor_id}/slots`, { params: { days: 21 } })
      .then((r) => {
        setDays(r.data.days);
        setSelectedDay(r.data.days[0]?.date ?? "");
      });
  }, [doctor.doctor_id]);

  async function confirm() {
    if (!slot) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post("/appointments", {
        doctor_id: doctor.doctor_id,
        scheduled_start: slot.start,
        visit_type: visitType,
        reason: reason || undefined,
        recommendation_id: recommendationId,
      });
      setDone(data);
    } catch (err) {
      setError(errorMessage(err, "Could not book that slot."));
    } finally {
      setBusy(false);
    }
  }

  const daySlots = days.find((d) => d.date === selectedDay)?.slots ?? [];

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink-900/50 p-4">
      <div className="w-full max-w-lg sp-card p-6 max-h-[90vh] overflow-y-auto">
        {done ? (
          <div className="text-center py-4">
            <Icon name="circleCheck" size={48} className="mx-auto text-ok-text" />
            <h3 className="text-xl font-bold text-ink-900 mt-3">
              Appointment confirmed
            </h3>
            <p className="text-ink-600 mt-1">
              {doctor.name} · {formatDate(done.scheduled_start)} at{" "}
              {new Date(done.scheduled_start).toLocaleTimeString("en-GB", {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </p>
            <p className="text-sm text-ink-500 mt-2">
              Your doctor can now see your structured intake before the visit.
            </p>
            <button className="sp-btn sp-btn-primary mt-5" onClick={onBooked}>
              Done
            </button>
          </div>
        ) : (
          <>
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-lg font-bold text-ink-900">Book appointment</h3>
                <p className="text-sm text-ink-500">
                  {doctor.name} · {doctor.specialty_name}
                </p>
              </div>
              <button onClick={onClose} className="text-2xl leading-none text-ink-400">
                ×
              </button>
            </div>

            <div className="mt-4 space-y-4">
              <div>
                <label className="sp-field">Consultation type</label>
                <div className="flex gap-2">
                  {["physical", "teleconsultation"].map((type) => (
                    <button
                      key={type}
                      onClick={() => setVisitType(type)}
                      disabled={
                        type === "teleconsultation" && !doctor.supports_teleconsultation
                      }
                      className={`flex-1 rounded-xl border px-3 py-2.5 text-sm font-medium transition disabled:opacity-40 ${
                        visitType === type
                          ? "border-brand-500 bg-brand-50 text-brand-800"
                          : "border-ink-200 text-ink-600"
                      }`}
                    >
                      {type === "physical" ? "In-person" : "Teleconsultation"}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="sp-field">Date</label>
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {days.slice(0, 14).map((day) => (
                    <button
                      key={day.date}
                      onClick={() => {
                        setSelectedDay(day.date);
                        setSlot(null);
                      }}
                      className={`shrink-0 rounded-xl border px-3 py-2 text-sm transition ${
                        selectedDay === day.date
                          ? "border-brand-500 bg-brand-50 text-brand-800 font-semibold"
                          : "border-ink-200 text-ink-600"
                      }`}
                    >
                      {new Date(day.date).toLocaleDateString("en-GB", {
                        weekday: "short",
                        day: "2-digit",
                        month: "short",
                      })}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="sp-field">Available times</label>
                {daySlots.length === 0 ? (
                  <p className="text-sm text-ink-500">No slots on this day.</p>
                ) : (
                  <div className="grid grid-cols-3 sm:grid-cols-4 gap-2 max-h-40 overflow-y-auto">
                    {daySlots.map((entry: any) => (
                      <button
                        key={entry.start}
                        onClick={() => setSlot(entry)}
                        className={`rounded-lg border px-2 py-2 text-sm transition ${
                          slot?.start === entry.start
                            ? "border-brand-500 bg-brand-600 text-white font-semibold"
                            : "border-ink-200 text-ink-700 hover:border-brand-400"
                        }`}
                      >
                        {entry.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <label className="sp-field" htmlFor="reason">Reason (optional)</label>
                <input
                  id="reason"
                  className="sp-input"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="e.g. Chest pain with breathlessness"
                />
              </div>

              <ErrorNote message={error} />

              <div className="flex gap-2">
                <button className="sp-btn sp-btn-secondary flex-1" onClick={onClose}>
                  Cancel
                </button>
                <button
                  className="sp-btn sp-btn-primary flex-1"
                  onClick={() => void confirm()}
                  disabled={!slot || busy}
                >
                  {busy ? "Booking…" : "Confirm booking"}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
