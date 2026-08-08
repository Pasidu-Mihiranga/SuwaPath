import { useEffect, useState } from "react";
import {
  Card,
  Empty,
  ErrorNote,
  Icon,
  Spinner,
  Stat,
  StatusChip,
  formatDateTime,
} from "../../components/ui";
import { api, errorMessage } from "../../lib/api";

function titleCase(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/* -------------------------------------------------------------- Overview */

export function AdminOverview() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/admin/overview")
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;
  if (!data) return <Empty title="Could not load the overview." />;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Platform Overview</h1>
        <p className="text-ink-500">SuwaPath system administration</p>
      </header>

      <div className="grid gap-3 sm:gap-4 grid-cols-2 lg:grid-cols-4">
        <Stat label="Total users" value={data.users.total.toLocaleString()} icon="person" />
        <Stat label="Doctors" value={data.providers.doctors} icon="stethoscope" />
        <Stat label="Hospitals" value={data.providers.hospitals} icon="hospital" />
        <Stat
          label="Diagnostic centres"
          value={data.providers.diagnostic_centres}
          icon="lab"
        />
      </div>

      <div className="grid lg:grid-cols-3 gap-4 lg:gap-6">
        <Card title="Users by role">
          <div className="space-y-2.5">
            {Object.entries(data.users.by_role).map(([role, count]) => (
              <div key={role} className="flex items-center justify-between">
                <span className="text-sm text-ink-600">{titleCase(role)}</span>
                <span className="font-semibold text-ink-900">
                  {(count as number).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Provider verification">
          <div className="space-y-2.5">
            {Object.entries(data.providers.verification).map(([status, count]) => (
              <div key={status} className="flex items-center justify-between">
                <StatusChip
                  value={status === "verified" ? "completed" : status}
                />
                <span className="font-semibold text-ink-900">
                  {count as number}
                </span>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Catalogues">
          <div className="space-y-2.5">
            {Object.entries(data.catalogues).map(([key, count]) => (
              <div key={key} className="flex items-center justify-between">
                <span className="text-sm text-ink-600">{titleCase(key)}</span>
                <span className="font-semibold text-ink-900">
                  {count as number}
                </span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card title="Platform activity">
        <div className="grid gap-3 sm:gap-4 grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
          {Object.entries(data.activity).map(([key, count]) => (
            <div key={key} className="rounded-xl bg-ink-50 p-3.5">
              <p className="text-xs text-ink-500">{titleCase(key)}</p>
              <p className="text-xl font-bold text-ink-900 mt-0.5">
                {(count as number).toLocaleString()}
              </p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

/* ----------------------------------------------------------------- Users */

export function AdminUsers() {
  const [data, setData] = useState<any>(null);
  const [role, setRole] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    const { data } = await api.get("/admin/users", {
      params: { role: role || undefined, search: search || undefined, limit: 50 },
    });
    setData(data);
    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, [role]);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Users & Roles</h1>
        <p className="text-ink-500">{data?.total?.toLocaleString() ?? 0} users</p>
      </header>

      <Card>
        <div className="flex flex-wrap gap-3">
          <form
            className="flex gap-2 flex-1 min-w-[220px]"
            onSubmit={(event) => {
              event.preventDefault();
              void load();
            }}
          >
            <input
              className="sp-input"
              placeholder="Search by name or email…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <button className="sp-btn sp-btn-secondary">Search</button>
          </form>
          <select
            className="sp-select w-auto"
            value={role}
            onChange={(event) => setRole(event.target.value)}
          >
            <option value="">All roles</option>
            {["patient", "guardian", "doctor", "hospital_admin", "system_admin"].map(
              (value) => (
                <option key={value} value={value}>
                  {titleCase(value)}
                </option>
              ),
            )}
          </select>
        </div>
      </Card>

      {loading ? (
        <Spinner />
      ) : (
        <Card className="!p-0 overflow-hidden">
          <div className="sp-table-wrap">
            <table className="w-full text-sm min-w-[680px]">
              <thead>
                <tr className="text-left text-ink-500 border-b border-ink-100">
                  <th className="py-2.5 px-4 font-medium">Name</th>
                  <th className="py-2.5 px-4 font-medium">Email</th>
                  <th className="py-2.5 px-4 font-medium">Role</th>
                  <th className="py-2.5 px-4 font-medium">Last login</th>
                  <th className="py-2.5 px-4 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-50">
                {data.users.map((user: any) => (
                  <tr key={user.id} className="hover:bg-ink-50">
                    <td className="py-3 px-4 font-medium text-ink-900">
                      {user.full_name}
                    </td>
                    <td className="py-3 px-4 text-ink-600">{user.email}</td>
                    <td className="py-3 px-4">
                      <span className="sp-chip bg-ink-100 text-ink-700">
                        {titleCase(user.role)}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-ink-500">
                      {user.last_login_at ? formatDateTime(user.last_login_at) : "Never"}
                    </td>
                    <td className="py-3 px-4">
                      <StatusChip value={user.is_active ? "completed" : "cancelled"} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

/* ------------------------------------------------- Provider verification */

export function AdminProviders() {
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const { data } = await api.get("/admin/providers/pending");
    setItems(data);
    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, []);

  async function verify(doctorId: string, status: string) {
    setError(null);
    try {
      await api.patch(`/admin/providers/${doctorId}/verify`, {
        verification_status: status,
      });
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not update verification."));
    }
  }

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Provider Verification</h1>
        <p className="text-ink-500">
          {items.length} provider(s) awaiting review
        </p>
      </header>

      <ErrorNote message={error} />

      {items.length === 0 ? (
        <Empty title="All providers verified" />
      ) : (
        <div className="grid gap-3 sm:gap-4 lg:grid-cols-2">
          {items.map((doctor) => (
            <Card key={doctor.doctor_id}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-bold text-ink-900">{doctor.name}</p>
                  <p className="text-sm text-ink-600">{doctor.specialty_name}</p>
                  <p className="text-sm text-ink-500">{doctor.hospital_name}</p>
                </div>
                <StatusChip value={doctor.verification_status} />
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
                <div>
                  <dt className="text-xs text-ink-500">SLMC registration</dt>
                  <dd className="text-ink-900">
                    {doctor.slmc_registration_no ?? "Not provided"}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-500">Experience</dt>
                  <dd className="text-ink-900">{doctor.years_experience} yrs</dd>
                </div>
              </dl>
              <div className="mt-4 flex gap-2">
                <button
                  className="sp-btn sp-btn-primary sp-btn-sm flex-1"
                  onClick={() => void verify(doctor.doctor_id, "verified")}
                >
                  Verify
                </button>
                <button
                  className="sp-btn sp-btn-secondary sp-btn-sm flex-1"
                  onClick={() => void verify(doctor.doctor_id, "rejected")}
                >
                  Reject
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ Facilities */

export function AdminFacilities() {
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/admin/hospitals")
      .then((r) => setItems(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Facilities</h1>
        <p className="text-ink-500">{items.length} registered facilities</p>
      </header>

      <Card className="!p-0 overflow-hidden">
        <div className="sp-table-wrap">
          <table className="w-full text-sm min-w-[720px]">
            <thead>
              <tr className="text-left text-ink-500 border-b border-ink-100">
                <th className="py-2.5 px-4 font-medium">Facility</th>
                <th className="py-2.5 px-4 font-medium">Type</th>
                <th className="py-2.5 px-4 font-medium">Location</th>
                <th className="py-2.5 px-4 font-medium">Emergency</th>
                <th className="py-2.5 px-4 font-medium">Services</th>
                <th className="py-2.5 px-4 font-medium">Beds</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-50">
              {items.map((facility) => (
                <tr key={facility.id} className="hover:bg-ink-50">
                  <td className="py-3 px-4 font-medium text-ink-900">
                    {facility.name}
                  </td>
                  <td className="py-3 px-4 text-ink-600">
                    {titleCase(facility.facility_type)}
                  </td>
                  <td className="py-3 px-4 text-ink-600">
                    {facility.city}, {facility.district}
                  </td>
                  <td className="py-3 px-4">
                    {facility.has_emergency ? (
                      <span className="sp-chip sp-chip-danger">24h</span>
                    ) : (
                      <span className="text-ink-400">—</span>
                    )}
                  </td>
                  <td className="py-3 px-4 text-ink-700">
                    {facility.capability_count}
                  </td>
                  <td className="py-3 px-4 text-ink-700">{facility.bed_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------- AI config */

export function AdminAi() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/admin/ai-config")
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;
  if (!data) return <Empty title="Could not load AI configuration." />;

  const orchestrator = data.orchestrator;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">AI Configuration</h1>
        <p className="text-ink-500">
          Live status of the orchestrator, knowledge retrieval and vision adapters.
        </p>
      </header>

      <div className="grid gap-3 sm:gap-4 grid-cols-2 sm:grid-cols-3">
        <Stat
          label="Gemini orchestrator"
          value={orchestrator.gemini_reachable ? "Live" : "Fallback"}
          tone={orchestrator.gemini_reachable ? "ok" : "warn"}
          hint={orchestrator.gemini_model ?? "Deterministic engine active"}
          icon="ai"
        />
        <Stat
          label="Knowledge retrieval"
          value={orchestrator.knowledge_backend.split("+")[0]}
          hint={orchestrator.knowledge_backend}
          tone="info"
          icon="description"
        />
        <Stat
          label="External search"
          value={orchestrator.tavily_configured ? "Enabled" : "Disabled"}
          tone={orchestrator.tavily_configured ? "ok" : "info"}
          hint="Never used for clinical decisions"
          icon="search"
        />
      </div>

      {!orchestrator.gemini_reachable && (
        <div className="sp-notice sp-notice-warn flex-col">
          <p className="font-semibold">
            Running in deterministic fallback mode
          </p>
          <p className="text-sm text-ink-700 mt-1">
            No Gemini API key is configured. Every feature still works — intake
            questions and explanations come from the rule-based engine instead of
            generated language. Set GEMINI_API_KEY to enable the live orchestrator.
          </p>
        </div>
      )}

      <Card
        title="Orchestration graph"
        subtitle="LangGraph — clinical decisions run as deterministic nodes"
      >
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-ink-500 mb-1.5">Clinical spine (ordered)</p>
            <div className="flex flex-wrap items-center gap-1.5">
              {data.graph.clinical_spine.map((node: string, index: number) => (
                <span key={node} className="flex items-center gap-1.5">
                  <span className="sp-chip bg-brand-100 text-brand-800">
                    {node.replace(/_/g, " ")}
                  </span>
                  {index < data.graph.clinical_spine.length - 1 && (
                    <Icon name="arrowRight" size={14} className="text-ink-300" />
                  )}
                </span>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <p className="text-xs text-ink-500 mb-1.5">LLM nodes</p>
              <div className="flex flex-wrap gap-1.5">
                {data.graph.llm_nodes.map((node: string) => (
                  <span key={node} className="sp-chip sp-chip-programme">
                    {node.replace(/_/g, " ")}
                  </span>
                ))}
              </div>
            </div>
            <div>
              <p className="text-xs text-ink-500 mb-1.5">Deterministic nodes</p>
              <div className="flex flex-wrap gap-1.5">
                {data.graph.deterministic_nodes.map((node: string) => (
                  <span key={node} className="sp-chip sp-chip-ok">
                    {node.replace(/_/g, " ")}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
        <p className="text-xs text-ink-500 mt-3">
          State store: {data.graph.checkpointer}
        </p>
      </Card>

      <Card
        title="Computer-vision adapters"
        subtitle="Highest-priority available adapter handles each modality"
      >
        <div className="sp-table-wrap -mx-4 px-4 sm:-mx-5 sm:px-5">
          <table className="w-full text-sm min-w-[620px]">
            <thead>
              <tr className="text-left text-ink-500 border-b border-ink-100">
                <th className="pb-2 font-medium">Adapter</th>
                <th className="pb-2 font-medium">Modality</th>
                <th className="pb-2 font-medium">Model</th>
                <th className="pb-2 font-medium">Trained</th>
                <th className="pb-2 font-medium">State</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-50">
              {data.vision_adapters.map((adapter: any) => (
                <tr key={adapter.adapter}>
                  <td className="py-2.5 font-medium text-ink-900">
                    {adapter.adapter}
                  </td>
                  <td className="py-2.5 text-ink-600">
                    {titleCase(adapter.modality)}
                  </td>
                  <td className="py-2.5 text-ink-600">{adapter.model_name}</td>
                  <td className="py-2.5">
                    {adapter.is_trained_model ? (
                      <span className="sp-chip sp-chip-ok">Trained</span>
                    ) : (
                      <span className="sp-chip sp-chip-warn">
                        Baseline
                      </span>
                    )}
                  </td>
                  <td className="py-2.5">
                    {adapter.active ? (
                      <span className="sp-chip bg-brand-100 text-brand-800">Active</span>
                    ) : adapter.available ? (
                      <span className="sp-chip bg-ink-100 text-ink-600">Standby</span>
                    ) : (
                      <span className="sp-chip bg-ink-100 text-ink-500">
                        No weights
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-ink-500 mt-3">
          Drop a trained model at <code>models/pneumonia/*.onnx</code> and the ONNX
          adapter takes over automatically.
        </p>
      </Card>

      <Card title="Stored configuration">
        <div className="space-y-3">
          {data.stored_config.map((config: any) => (
            <details key={config.key} className="rounded-xl border border-ink-100 p-3.5">
              <summary className="cursor-pointer font-medium text-ink-900">
                {config.key}
                <span className="ml-2 text-xs font-normal text-ink-500">
                  {config.description}
                </span>
              </summary>
              <pre className="mt-2 text-xs bg-ink-50 rounded-lg p-3 overflow-x-auto text-ink-700">
                {JSON.stringify(config.value, null, 2)}
              </pre>
            </details>
          ))}
        </div>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------- Audit log */

export function AdminAudit() {
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/admin/audit-logs", { params: { limit: 100 } })
      .then((r) => setItems(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Audit Log</h1>
        <p className="text-ink-500">System-level activity trail</p>
      </header>

      {items.length === 0 ? (
        <Empty title="No audit entries" />
      ) : (
        <Card className="!p-0 overflow-hidden">
          <div className="sp-table-wrap">
            <table className="w-full text-sm min-w-[640px]">
              <thead>
                <tr className="text-left text-ink-500 border-b border-ink-100">
                  <th className="py-2.5 px-4 font-medium">When</th>
                  <th className="py-2.5 px-4 font-medium">Action</th>
                  <th className="py-2.5 px-4 font-medium">Actor</th>
                  <th className="py-2.5 px-4 font-medium">Resource</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-50">
                {items.map((log) => (
                  <tr key={log.id} className="hover:bg-ink-50">
                    <td className="py-3 px-4 text-ink-600">
                      {formatDateTime(log.created_at)}
                    </td>
                    <td className="py-3 px-4">
                      <span className="sp-chip bg-ink-100 text-ink-700">
                        {log.action}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-ink-800">
                      {log.actor_name ?? "System"}
                      <span className="block text-xs text-ink-400">
                        {log.actor_role}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-ink-600">
                      {log.resource_type ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
