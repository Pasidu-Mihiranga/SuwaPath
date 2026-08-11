import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AiNotice,
  Card,
  Confidence,
  Icon,
  Empty,
  ErrorNote,
  Spinner,
  StatusChip,
  formatDateTime,
} from "../../components/ui";
import { API_BASE, api, errorMessage, tokens } from "../../lib/api";

/* ---------------------------------------------------------------- Reports */

export function Reports() {
  const navigate = useNavigate();
  const [items, setItems] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    const { data } = await api.get("/documents");
    setItems(data);
    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, []);

  async function upload(file: File) {
    setUploading(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    form.append("document_type", "lab_report");
    try {
      const { data } = await api.post("/documents", form, { timeout: 120000 });
      setSelected(data);
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not process that document."));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-ink-900">Medical Reports</h1>
          <p className="text-ink-500">
            Upload a lab report, prescription or radiology report. We read it and
            explain it in plain language.
          </p>
        </div>
        <label className="sp-btn sp-btn-primary cursor-pointer">
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.jpg,.jpeg,.png"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void upload(file);
            }}
          />
          {uploading ? "Processing…" : "Upload report"}
        </label>
      </header>

      <ErrorNote message={error} />
      {uploading && <Spinner label="Reading your report…" />}

      {selected && <ReportDetail document={selected} onFindCare={(id) => navigate(`/patient/find-care?recommendation=${id}`)} />}

      <Card title="Your reports">
        {loading ? (
          <Spinner />
        ) : items.length === 0 ? (
          <Empty title="No reports uploaded yet" hint="PDF, JPG and PNG are supported." />
        ) : (
          <div className="divide-y divide-ink-100">
            {items.map((item) => (
              <button
                key={item.id}
                className="w-full text-left py-3.5 flex items-center gap-4 hover:bg-ink-50 -mx-2 px-2 rounded-lg transition"
                onClick={async () => {
                  const { data } = await api.get(`/documents/${item.id}`);
                  setSelected(data);
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
              >
                <span className="sp-icon-tile bg-brand-50 text-brand-700"><Icon name="description" size={20} /></span>
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-ink-900 truncate">
                    {item.file_name}
                  </p>
                  <p className="text-xs text-ink-500">
                    {formatDateTime(item.uploaded_at)} ·{" "}
                    {item.document_type.replace(/_/g, " ")}
                  </p>
                </div>
                {item.abnormal_count > 0 ? (
                  <span className="sp-chip sp-chip-warn">
                    {item.abnormal_count} to review
                  </span>
                ) : (
                  <StatusChip value={item.processing_status} />
                )}
              </button>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function ReportDetail({
  document,
  onFindCare,
}: {
  document: any;
  onFindCare: (recommendationId: string) => void;
}) {
  const extracted = document.extracted;
  if (!extracted) return null;

  return (
    <div className="space-y-4">
      <Card
        title={extracted.report_title ?? document.file_name}
        subtitle={[extracted.facility_name, extracted.collection_date]
          .filter(Boolean)
          .join(" · ")}
        action={
          <span className="sp-chip bg-ink-100 text-ink-700">
            {extracted.ocr_engine} · {Math.round(extracted.ocr_confidence * 100)}%
          </span>
        }
      >
        {/* Table rows keep their original order and printed reference ranges */}
        <div className="sp-table-wrap -mx-4 px-4 sm:-mx-5 sm:px-5">
          <table className="w-full text-sm min-w-[560px]">
            <thead>
              <tr className="text-left text-ink-500 border-b border-ink-100">
                <th className="pb-2 font-medium">Test</th>
                <th className="pb-2 font-medium">Result</th>
                <th className="pb-2 font-medium">Reference range</th>
                <th className="pb-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-50">
              {extracted.values.map((value: any, index: number) => (
                <tr key={index}>
                  <td className="py-2.5 text-ink-800">{value.test_name}</td>
                  <td className="py-2.5 font-semibold text-ink-900">
                    {value.result}
                  </td>
                  <td className="py-2.5 text-ink-600">
                    {value.reference_range ?? "—"}
                    {value.reference_source === "report" && (
                      <span className="ml-1.5 text-[10px] text-ink-400">
                        (from report)
                      </span>
                    )}
                  </td>
                  <td className="py-2.5">
                    <StatusChip value={value.flag} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-5 rounded-xl bg-brand-50 border border-brand-200 p-4">
          <p className="text-sm font-semibold text-brand-900">
            Plain-language explanation
          </p>
          <p className="text-sm text-ink-700 mt-1.5">
            {extracted.plain_language_explanation}
          </p>
        </div>
        <AiNotice>
          Values are compared against the reference range printed on your own
          report. Ranges differ between laboratories.
        </AiNotice>
      </Card>

      {document.recommendation && (
        <Card title="Suggested next step">
          <p className="text-sm text-ink-700">
            {document.recommendation.suggested_next_action}
          </p>
          <div className="mt-3 flex items-center justify-between">
            <div>
              <p className="text-xs text-ink-500">Suggested specialty</p>
              <p className="font-semibold text-ink-900">
                {document.recommendation.specialty_code.replace(/_/g, " ")}
              </p>
            </div>
            <Confidence value={document.recommendation.confidence} />
          </div>
          <button
            className="sp-btn sp-btn-primary mt-4"
            onClick={() => onFindCare(document.recommendation.id)}
          >
            See matching doctors
            <Icon name="arrowRight" size={18} />
          </button>
        </Card>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- Imaging */

export function Imaging() {
  const navigate = useNavigate();
  const [items, setItems] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modality, setModality] = useState("chest_xray");

  async function load() {
    const { data } = await api.get("/images");
    setItems(data);
    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, []);

  async function upload(file: File) {
    setUploading(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    form.append("modality", modality);
    try {
      const { data } = await api.post("/images", form, { timeout: 120000 });
      setSelected(data);
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not screen that image."));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Medical Image Screening</h1>
        <p className="text-ink-500">
          AI-assisted screening support. This is not a diagnosis — a clinician
          must review the original image.
        </p>
      </header>

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="sp-field">Image type</label>
            <select
              className="sp-select w-auto"
              value={modality}
              onChange={(event) => setModality(event.target.value)}
            >
              <option value="chest_xray">Chest X-ray</option>
            </select>
          </div>
          <label className="sp-btn sp-btn-primary cursor-pointer">
            <input
              type="file"
              accept=".jpg,.jpeg,.png"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void upload(file);
              }}
            />
            {uploading ? "Analysing…" : "Upload image"}
          </label>
          <p className="text-xs text-ink-500 flex-1 min-w-[200px]">
            Only supported modalities are accepted. Images that do not look like
            the selected type are rejected before analysis.
          </p>
        </div>
      </Card>

      <ErrorNote message={error} />
      {uploading && <Spinner label="Running the screening model…" />}

      {selected?.analysis && (
        <Card title="Screening result">
          <div className="grid md:grid-cols-2 gap-5">
            <div className="space-y-3">
              {/* AuthedImage, not a plain <img>: this endpoint requires a
                  bearer token, which a browser image request never sends, so
                  the tag below used to 401 and render nothing. The heatmap
                  underneath already did it correctly. */}
              <AuthedImage
                src={`${API_BASE}/api/v1/images/${selected.id}/file`}
                alt="Uploaded medical image"
                className="w-full rounded-xl border border-ink-100 bg-ink-900"
              />
              {selected.analysis.has_visual_explanation && (
                <div>
                  <p className="text-xs text-ink-500 mb-1.5">
                    Visual explanation — regions that most influenced the result
                  </p>
                  <AuthedImage
                    src={`${API_BASE}/api/v1/images/${selected.id}/heatmap`}
                  />
                </div>
              )}
            </div>

            <div className="space-y-4">
              <div>
                <p className="text-xs text-ink-500">Possible finding</p>
                <p className="text-lg font-bold text-ink-900">
                  {selected.analysis.finding_label}
                </p>
              </div>
              <div>
                <p className="text-xs text-ink-500 mb-1">Confidence</p>
                <Confidence value={selected.analysis.confidence} />
              </div>

              {selected.analysis.is_uncertain && (
                <div className="sp-notice sp-notice-warn flex-col">
                  <p className="text-sm font-semibold">
                    Result is uncertain
                  </p>
                  <p className="text-sm text-ink-700 mt-0.5">
                    {selected.analysis.uncertainty_note}
                  </p>
                </div>
              )}

              <p className="text-sm text-ink-600">
                {selected.analysis.finding_description}
              </p>

              <ImageFeatures analysis={selected.analysis} />

              <div className="rounded-xl bg-brand-50 border border-brand-200 p-3">
                <p className="text-xs font-semibold text-brand-900">
                  Suggested next step
                </p>
                <p className="text-sm text-ink-700 mt-0.5">
                  {selected.analysis.suggested_next_step}
                </p>
              </div>

              <div className="text-xs text-ink-500 space-y-0.5">
                <p>
                  Model: {selected.analysis.model_name} (
                  {selected.analysis.model_version})
                </p>
                <p>Inference time: {selected.analysis.inference_ms} ms</p>
              </div>

              {selected.recommendation && (
                <button
                  className="sp-btn sp-btn-primary"
                  onClick={() =>
                    navigate(
                      `/patient/find-care?recommendation=${selected.recommendation.id}`,
                    )
                  }
                >
                  See matching specialists
                  <Icon name="arrowRight" size={18} />
                </button>
              )}
            </div>
          </div>
          <AiNotice>{selected.analysis.disclaimer}</AiNotice>
        </Card>
      )}

      <Card title="Previous screenings">
        {loading ? (
          <Spinner />
        ) : items.length === 0 ? (
          <Empty title="No images screened yet" />
        ) : (
          <div className="divide-y divide-ink-100">
            {items.map((item) => (
              <button
                key={item.id}
                className="w-full text-left py-3.5 flex items-center gap-4 hover:bg-ink-50 -mx-2 px-2 rounded-lg transition"
                onClick={async () => {
                  const { data } = await api.get(`/images/${item.id}`);
                  setSelected(data);
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
              >
                <span className="sp-icon-tile bg-programme-surface text-programme-text"><Icon name="scan" size={20} /></span>
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-ink-900">
                    {item.analysis?.finding_label ?? item.file_name}
                  </p>
                  <p className="text-xs text-ink-500">
                    {item.modality.replace(/_/g, " ")} ·{" "}
                    {formatDateTime(item.uploaded_at)}
                  </p>
                </div>
                {item.analysis && (
                  <Confidence value={item.analysis.confidence} />
                )}
              </button>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

/**
 * What the screening actually measured, and where the decision boundary sits.
 *
 * A bare probability is unreadable and unfalsifiable — a reader can neither
 * agree nor disagree with "0.82". The underlying quantities are what let a
 * clinician say "the asymmetry is doing the work here, and that is a rotation
 * artefact". Adapters that cannot decompose their output return no
 * measurements and this renders nothing.
 */
function ImageFeatures({ analysis }: { analysis: any }) {
  const measurements: any[] = analysis.measurements ?? [];
  if (measurements.length === 0) return null;

  // Contributions are signed log-odds terms, so scale bars by the largest
  // magnitude present rather than by a fixed maximum: a feature arguing
  // against the finding is as informative as one arguing for it.
  const peak = Math.max(
    ...measurements.map((m) => Math.abs(Number(m.contribution) || 0)),
    0.001,
  );
  const probability = analysis.class_probabilities?.pneumonia;
  const threshold = analysis.decision_threshold;

  return (
    <div className="rounded-xl border border-line p-3">
      <p className="text-xs font-semibold text-ink-900">What the model measured</p>
      <p className="text-xs text-ink-500 mt-0.5">
        Radiographic features computed from the pixels, and how much each moved
        the score.
      </p>

      <div className="mt-3 space-y-2.5">
        {measurements.map((m) => {
          const contribution = Number(m.contribution) || 0;
          const width = (Math.abs(contribution) / peak) * 100;
          return (
            <div key={m.code}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm text-ink-800">{m.label}</span>
                <span className="text-xs tabular-nums text-ink-500">
                  {Number(m.value).toFixed(3)}
                </span>
              </div>
              <div className="mt-1 h-1.5 rounded-full bg-surface overflow-hidden">
                <div
                  className={`h-full rounded-full ${contribution >= 0 ? "bg-brand-500" : "bg-ink-300"}`}
                  style={{ width: `${width}%` }}
                />
              </div>
              <p className="mt-1 text-xs text-ink-500">{m.detail}</p>
            </div>
          );
        })}
      </div>

      {typeof probability === "number" && typeof threshold === "number" && (
        <p className="mt-3 border-t border-line pt-2 text-xs text-ink-500">
          Score {probability.toFixed(3)} against a decision threshold of{" "}
          {threshold.toFixed(3)}.
          {threshold < 0.4 &&
            " Tuned for sensitivity, so it raises more false alarms by design."}
        </p>
      )}
    </div>
  );
}

/** Image endpoints require a bearer token, so fetch as a blob. */
function AuthedImage({
  src,
  alt = "Model attention heatmap",
  className = "w-full rounded-xl border border-ink-100",
}: {
  src: string;
  alt?: string;
  className?: string;
}) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let revoked: string | null = null;
    fetch(src, { headers: { Authorization: `Bearer ${tokens.access}` } })
      .then((response) => (response.ok ? response.blob() : Promise.reject()))
      .then((blob) => {
        revoked = URL.createObjectURL(blob);
        setUrl(revoked);
      })
      .catch(() => setUrl(null));
    return () => {
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [src]);

  if (!url) return null;
  return <img src={url} alt={alt} className={className} />;
}
