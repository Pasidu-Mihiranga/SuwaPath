"""Medical document intelligence: READ -> STRUCTURE -> EXPLAIN -> NAVIGATE.

Pipeline
--------
1. Read      PDFs use their embedded text layer when present (fast and exact);
             scanned pages and images fall back to Tesseract OCR.
2. Structure Lines are parsed into analyte rows preserving table row order,
             with units and reference ranges.
3. Explain   Values are flagged against **the reference range printed on the
             report**, falling back to a catalogue range only when the report
             does not carry one. Explanation is grounded in the knowledge
             collection.
4. Navigate  The result feeds the shared care-navigation engine (rule 5).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.models.enums import DocumentType, ResultFlag

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Analyte catalogue: fallback ranges + which specialty an abnormality suggests
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class AnalyteDef:
    canonical: str
    aliases: tuple[str, ...]
    unit: str
    low: float | None
    high: float | None
    specialty: str
    plain_name: str


ANALYTES: list[AnalyteDef] = [
    AnalyteDef("haemoglobin", ("hb", "hgb", "haemoglobin", "hemoglobin"), "g/dL",
               12.0, 16.0, "general_medicine", "haemoglobin (oxygen-carrying protein)"),
    AnalyteDef("wbc", ("wbc", "white blood cell", "white cell count", "leucocyte",
                       "total wbc"), "/µL", 4000, 11000, "general_medicine",
               "white blood cells (infection-fighting cells)"),
    AnalyteDef("platelets", ("platelet", "plt", "platelet count"), "/µL",
               150000, 450000, "general_medicine", "platelets (clotting cells)"),
    AnalyteDef("tsh", ("tsh", "thyroid stimulating hormone"), "mIU/L", 0.4, 4.0,
               "endocrinology", "TSH (thyroid control hormone)"),
    AnalyteDef("free_t4", ("free t4", "ft4", "free thyroxine"), "ng/dL", 0.8, 1.8,
               "endocrinology", "free T4 (active thyroid hormone)"),
    AnalyteDef("anti_tpo", ("anti-tpo", "anti tpo", "tpo antibody"), "IU/mL",
               None, 35, "endocrinology", "anti-TPO antibodies (thyroid autoimmunity)"),
    AnalyteDef("fbs", ("fbs", "fasting blood sugar", "fasting glucose",
                       "fasting plasma glucose"), "mg/dL", 70, 100,
               "endocrinology", "fasting blood sugar"),
    AnalyteDef("hba1c", ("hba1c", "glycated haemoglobin", "a1c"), "%", None, 5.7,
               "endocrinology", "HbA1c (3-month average blood sugar)"),
    AnalyteDef("total_cholesterol", ("total cholesterol", "cholesterol total"),
               "mg/dL", None, 200, "cardiology", "total cholesterol"),
    AnalyteDef("ldl", ("ldl", "ldl cholesterol"), "mg/dL", None, 100,
               "cardiology", "LDL ('bad') cholesterol"),
    AnalyteDef("hdl", ("hdl", "hdl cholesterol"), "mg/dL", 40, None,
               "cardiology", "HDL ('good') cholesterol"),
    AnalyteDef("triglycerides", ("triglyceride", "tg"), "mg/dL", None, 150,
               "cardiology", "triglycerides (blood fats)"),
    AnalyteDef("creatinine", ("creatinine", "serum creatinine"), "mg/dL", 0.6, 1.3,
               "general_medicine", "creatinine (kidney function marker)"),
    AnalyteDef("urea", ("urea", "blood urea", "bun"), "mg/dL", 7, 20,
               "general_medicine", "urea (kidney function marker)"),
    AnalyteDef("alt", ("alt", "sgpt", "alanine aminotransferase"), "U/L", None, 40,
               "gastroenterology", "ALT (liver enzyme)"),
    AnalyteDef("ast", ("ast", "sgot", "aspartate aminotransferase"), "U/L", None, 40,
               "gastroenterology", "AST (liver enzyme)"),
    AnalyteDef("bilirubin", ("bilirubin", "total bilirubin"), "mg/dL", None, 1.2,
               "gastroenterology", "bilirubin (liver/bile marker)"),
    AnalyteDef("crp", ("crp", "c-reactive protein", "c reactive protein"), "mg/L",
               None, 5, "general_medicine", "CRP (inflammation marker)"),
    AnalyteDef("ferritin", ("ferritin", "serum ferritin"), "ng/mL", 30, 300,
               "general_medicine", "ferritin (iron stores)"),
    AnalyteDef("serum_iron", ("serum iron", "iron"), "µg/dL", 60, 170,
               "general_medicine", "serum iron"),
    AnalyteDef("vitamin_d", ("vitamin d", "25-oh", "25 oh vitamin d",
                             "vitamin d (25-oh)"), "ng/mL", 30, 100,
               "general_medicine", "vitamin D"),
    AnalyteDef("esr", ("esr", "sedimentation rate"), "mm/hr", None, 20,
               "general_medicine", "ESR (inflammation marker)"),
]

ANALYTE_LOOKUP: dict[str, AnalyteDef] = {}
for _analyte in ANALYTES:
    for _alias in _analyte.aliases:
        ANALYTE_LOOKUP[_alias] = _analyte


@dataclass
class ParsedValue:
    test_name: str
    normalised_name: str | None
    result_text: str
    result_numeric: float | None
    unit: str | None
    reference_range_text: str | None
    reference_low: float | None
    reference_high: float | None
    reference_source: str  # "report" | "catalogue" | "none"
    flag: ResultFlag
    row_index: int

    def to_dict(self) -> dict:
        return {
            "test_name": self.test_name,
            "normalised_name": self.normalised_name,
            "result": self.result_text,
            "result_numeric": self.result_numeric,
            "unit": self.unit,
            "reference_range": self.reference_range_text,
            "reference_source": self.reference_source,
            "flag": str(self.flag),
            "row_index": self.row_index,
        }


@dataclass
class ExtractionResult:
    engine: str
    raw_text: str
    page_count: int
    ocr_confidence: float
    values: list[ParsedValue] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    report_title: str | None = None
    facility_name: str | None = None
    patient_name: str | None = None
    collection_date: str | None = None
    document_type: DocumentType = DocumentType.LAB_REPORT


# --------------------------------------------------------------------------
# 1. Read
# --------------------------------------------------------------------------
def read_document(path: Path, mime_type: str) -> tuple[str, int, float, str]:
    """Return (text, page_count, confidence, engine)."""
    suffix = path.suffix.lower()

    if suffix == ".pdf" or "pdf" in mime_type:
        return _read_pdf(path)
    return _read_image(path)


def _read_pdf(path: Path) -> tuple[str, int, float, str]:
    import fitz

    doc = fitz.open(path)
    page_count = doc.page_count
    pages_text: list[str] = []
    used_ocr = False

    for page in doc:
        text = page.get_text("text") or ""
        # A near-empty text layer means the page is a scan; OCR it instead.
        if len(text.strip()) < 40:
            try:
                pixmap = page.get_pixmap(dpi=220)
                image_bytes = pixmap.tobytes("png")
                text = _ocr_bytes(image_bytes)
                used_ocr = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Page OCR failed: %s", exc)
        pages_text.append(text)

    doc.close()
    full_text = "\n".join(pages_text)
    engine = "tesseract" if used_ocr else "pymupdf-textlayer"
    # A native text layer is exact; OCR confidence is estimated below.
    confidence = 0.99 if not used_ocr else _estimate_confidence(full_text)
    return full_text, page_count, confidence, engine


def _read_image(path: Path) -> tuple[str, int, float, str]:
    text = _ocr_bytes(path.read_bytes())
    return text, 1, _estimate_confidence(text), "tesseract"


def _ocr_bytes(data: bytes) -> str:
    import io

    import pytesseract
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    # PSM 6 assumes a uniform block of text, which suits tabular lab reports.
    return pytesseract.image_to_string(image, config="--psm 6")


def _estimate_confidence(text: str) -> float:
    """Rough OCR quality proxy from the ratio of recognisable characters."""
    if not text.strip():
        return 0.0
    total = len(text)
    good = sum(1 for c in text if c.isalnum() or c.isspace() or c in ".,:;/()-%<>")
    return round(min(0.97, good / total), 3)


# --------------------------------------------------------------------------
# 2. Structure
# --------------------------------------------------------------------------
# A lab row: name, result, optional unit, optional reference range.
_NUMBER = r"[<>]?\s*\d[\d,]*\.?\d*"
_RANGE_PATTERNS = [
    re.compile(rf"({_NUMBER})\s*(?:-|–|—|to)\s*({_NUMBER})"),
    re.compile(rf"(?:<|less than)\s*({_NUMBER})"),
    re.compile(rf"(?:>|greater than)\s*({_NUMBER})"),
]
_UNIT_PATTERN = re.compile(
    r"\b(g/dL|g/dl|mg/dL|mg/dl|mmol/L|mIU/L|µIU/mL|uIU/mL|ng/dL|ng/mL|pg/mL|"
    r"IU/mL|U/L|mm/hr|mg/L|%|/µL|/uL|cells/µL|10\^3/µL|x10\^9/L|µg/dL|ug/dL)\b"
)


def parse_values(text: str) -> tuple[list[ParsedValue], list[dict]]:
    """Parse analyte rows out of report text, preserving row order."""
    values: list[ParsedValue] = []
    rows: list[dict] = []
    row_index = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if len(line) < 4:
            continue
        # Skip obvious header rows.
        if re.match(r"^(test|investigation|parameter|analyte)\b", line, re.IGNORECASE):
            continue

        parsed = _parse_line(line, row_index)
        if parsed is None:
            continue

        values.append(parsed)
        rows.append(
            {
                "row_index": row_index,
                "raw": line,
                "test": parsed.test_name,
                "result": parsed.result_text,
                "unit": parsed.unit,
                "reference_range": parsed.reference_range_text,
                "flag": str(parsed.flag),
            }
        )
        row_index += 1

    return values, rows


def _parse_line(line: str, row_index: int) -> ParsedValue | None:
    # Normalise separators so both spaced and pipe/tab tables parse.
    working = re.sub(r"[|\t]+", "  ", line)

    parsed = _parse_columns(working) or _parse_inline(working)
    if parsed is None:
        return None
    name, result_text, unit, reference_text, ref_low, ref_high = parsed

    if not name or len(name) < 2 or len(name) > 80:
        return None
    # A line that is mostly digits is not an analyte row.
    if sum(c.isalpha() for c in name) < 2:
        return None

    result_numeric = _to_float(result_text)
    analyte = _match_analyte(name)

    # Reject document metadata ("Sample No : LC-2026-88214", "Age / Sex : 32").
    # A genuine analyte row carries at least one of: a printed reference range,
    # a recognised unit, or a name matching the analyte catalogue.
    has_printed_range = ref_low is not None or ref_high is not None
    if not (has_printed_range or unit or analyte):
        return None

    # The range printed on the report always wins (spec §11).
    reference_source = "none"
    if ref_low is not None or ref_high is not None:
        reference_source = "report"
    elif analyte and (analyte.low is not None or analyte.high is not None):
        ref_low, ref_high = analyte.low, analyte.high
        reference_text = _format_range(ref_low, ref_high)
        reference_source = "catalogue"

    return ParsedValue(
        test_name=name,
        normalised_name=analyte.canonical if analyte else None,
        result_text=(result_text + (f" {unit}" if unit else "")).strip(),
        result_numeric=result_numeric,
        unit=unit or (analyte.unit if analyte else None),
        reference_range_text=reference_text,
        reference_low=ref_low,
        reference_high=ref_high,
        reference_source=reference_source,
        flag=_flag_value(result_numeric, ref_low, ref_high),
        row_index=row_index,
    )


# Parsed row: (name, result_text, unit, reference_text, ref_low, ref_high)
_Row = tuple[str, str, str | None, str | None, float | None, float | None]


def _parse_columns(line: str) -> _Row | None:
    """Parse a column-formatted row, split on runs of 2+ spaces.

    Column layout is the reliable signal in lab reports and, unlike scanning
    for the first number, it keeps analyte names containing digits intact
    ("Free T4", "HbA1c", "Vitamin D (25-OH)").
    """
    columns = [c.strip() for c in re.split(r"\s{2,}", line) if c.strip()]
    if len(columns) < 2:
        return None

    name = columns[0].strip(" .:—-")
    rest = columns[1:]

    # The result is the first remaining column that is a bare number.
    result_text: str | None = None
    result_index = -1
    for index, column in enumerate(rest):
        if re.fullmatch(rf"{_NUMBER}", column.strip()):
            result_text = column.strip()
            result_index = index
            break
    if result_text is None:
        return None

    tail = rest[result_index + 1 :]
    unit = next(
        (c for c in tail if _UNIT_PATTERN.fullmatch(c.strip())),
        None,
    )
    reference_text = ref_low = ref_high = None
    for column in tail:
        text, low, high, span = _extract_reference(column)
        if span is not None:
            reference_text, ref_low, ref_high = text, low, high
            break

    return name, result_text, unit, reference_text, ref_low, ref_high


def _parse_inline(line: str) -> _Row | None:
    """Fallback for single-spaced rows: locate the range, unit, then result."""
    reference_text, ref_low, ref_high, ref_span = _extract_reference(line)
    remainder = line[: ref_span[0]] + " " + line[ref_span[1] :] if ref_span else line

    unit_match = _UNIT_PATTERN.search(remainder)
    unit = unit_match.group(0) if unit_match else None
    if unit_match:
        remainder = remainder[: unit_match.start()] + " " + remainder[unit_match.end() :]

    # Prefer the last number before the unit/range, so digits inside the
    # analyte name are not mistaken for the result.
    matches = list(re.finditer(rf"({_NUMBER})", remainder))
    if not matches:
        return None
    result_match = matches[-1]

    name = remainder[: result_match.start()].strip(" .:—-")
    return name, result_match.group(1).strip(), unit, reference_text, ref_low, ref_high


def _extract_reference(
    text: str,
) -> tuple[str | None, float | None, float | None, tuple[int, int] | None]:
    for index, pattern in enumerate(_RANGE_PATTERNS):
        match = pattern.search(text)
        if not match:
            continue
        if index == 0:
            low, high = _to_float(match.group(1)), _to_float(match.group(2))
        elif index == 1:
            low, high = None, _to_float(match.group(1))
        else:
            low, high = _to_float(match.group(1)), None
        return match.group(0).strip(), low, high, match.span()
    return None, None, None, None


def _match_analyte(name: str) -> AnalyteDef | None:
    lowered = re.sub(r"[^a-z0-9\s\-()]", "", name.lower()).strip()
    if lowered in ANALYTE_LOOKUP:
        return ANALYTE_LOOKUP[lowered]
    # Longest alias contained in the name wins, avoiding "iron" matching
    # "serum iron" incorrectly.
    best: AnalyteDef | None = None
    best_length = 0
    for alias, analyte in ANALYTE_LOOKUP.items():
        if alias in lowered and len(alias) > best_length:
            best, best_length = analyte, len(alias)
    return best


def _flag_value(
    value: float | None, low: float | None, high: float | None
) -> ResultFlag:
    if value is None or (low is None and high is None):
        return ResultFlag.UNKNOWN
    if low is not None and value < low:
        # Markedly below range is treated as critical for triage purposes.
        return ResultFlag.CRITICAL if value < low * 0.6 else ResultFlag.LOW
    if high is not None and value > high:
        return ResultFlag.CRITICAL if value > high * 2.0 else ResultFlag.HIGH
    return ResultFlag.NORMAL


def _to_float(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = re.sub(r"[<>,\s]", "", text)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _format_range(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{_trim(low)} - {_trim(high)}"
    if high is not None:
        return f"< {_trim(high)}"
    if low is not None:
        return f"> {_trim(low)}"
    return ""


def _trim(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------
_DATE_PATTERN = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b",
    re.IGNORECASE,
)


def extract_metadata(text: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    metadata: dict = {
        "report_title": None,
        "facility_name": None,
        "patient_name": None,
        "collection_date": None,
    }

    for line in lines[:12]:
        lowered = line.lower()
        if metadata["patient_name"] is None:
            match = re.search(
                r"(?:patient\s*name|patient|name)\s*[:\-]\s*(.+)", line, re.IGNORECASE
            )
            if match:
                metadata["patient_name"] = match.group(1).strip()[:120]
                continue
        if metadata["facility_name"] is None and any(
            word in lowered
            for word in ("hospital", "laboratory", "labs", "diagnostic", "medical centre",
                         "medical center", "clinic")
        ):
            metadata["facility_name"] = line[:160]
            continue
        if metadata["report_title"] is None and any(
            word in lowered
            for word in ("report", "profile", "panel", "count", "test results")
        ):
            metadata["report_title"] = line[:160]

    date_match = _DATE_PATTERN.search(text)
    if date_match:
        metadata["collection_date"] = date_match.group(1)

    return metadata


def classify_document(text: str) -> DocumentType:
    lowered = (text or "").lower()
    if any(w in lowered for w in ("prescription", "rx", "sig:", "tablets to be taken")):
        return DocumentType.PRESCRIPTION
    if any(w in lowered for w in ("x-ray", "ultrasound", "ct scan", "mri", "radiolog",
                                  "impression:", "sonograph")):
        return DocumentType.RADIOLOGY_REPORT
    if any(w in lowered for w in ("discharge summary", "discharged on", "admission date")):
        return DocumentType.DISCHARGE_SUMMARY
    if any(w in lowered for w in ("reference range", "laboratory", "specimen",
                                  "haemoglobin", "hemoglobin", "wbc", "result")):
        return DocumentType.LAB_REPORT
    return DocumentType.CLINICAL_REPORT


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def extract_document(path: Path, mime_type: str) -> ExtractionResult:
    text, page_count, confidence, engine = read_document(path, mime_type)
    values, tables = parse_values(text)
    metadata = extract_metadata(text)

    return ExtractionResult(
        engine=engine,
        raw_text=text,
        page_count=page_count,
        ocr_confidence=confidence,
        values=values,
        tables=tables,
        report_title=metadata["report_title"],
        facility_name=metadata["facility_name"],
        patient_name=metadata["patient_name"],
        collection_date=metadata["collection_date"],
        document_type=classify_document(text),
    )


# --------------------------------------------------------------------------
# 3. Explain
# --------------------------------------------------------------------------
def summarise(result: ExtractionResult) -> dict:
    """Plain-language summary, key findings and the specialty to route to."""
    abnormal = [
        v for v in result.values
        if v.flag in (ResultFlag.LOW, ResultFlag.HIGH, ResultFlag.CRITICAL)
    ]
    normal_count = sum(1 for v in result.values if v.flag == ResultFlag.NORMAL)

    key_findings = [
        {
            "test": v.test_name,
            "result": v.result_text,
            "reference_range": v.reference_range_text,
            "flag": str(v.flag),
            "plain_name": (
                ANALYTE_LOOKUP.get(v.normalised_name or "", None).plain_name
                if v.normalised_name and v.normalised_name in ANALYTE_LOOKUP
                else v.test_name
            ),
        }
        for v in abnormal[:8]
    ]

    specialty = _suggest_specialty(abnormal)
    explanation = _build_explanation(abnormal, normal_count, len(result.values))

    if abnormal:
        next_step = (
            f"Discuss these results with a doctor. Based on which values are "
            f"outside the range shown on your report, a consultation in "
            f"{specialty.replace('_', ' ')} may be appropriate."
        )
    else:
        next_step = (
            "All detected values sit within the reference ranges printed on "
            "your report. Discuss with your doctor if you still have symptoms."
        )

    return {
        "summary": _build_summary(result, abnormal, normal_count),
        "explanation": explanation,
        "key_findings": key_findings,
        "suggested_specialty_code": specialty,
        "possible_next_step": next_step,
        "abnormal_count": len(abnormal),
        "normal_count": normal_count,
    }


def _build_summary(
    result: ExtractionResult, abnormal: list[ParsedValue], normal_count: int
) -> str:
    title = result.report_title or "Medical report"
    parts = [f"{title}: {len(result.values)} result(s) detected."]
    if abnormal:
        names = ", ".join(v.test_name for v in abnormal[:4])
        parts.append(f"{len(abnormal)} value(s) outside the printed reference range ({names}).")
    if normal_count:
        parts.append(f"{normal_count} value(s) within range.")
    return " ".join(parts)


def _build_explanation(
    abnormal: list[ParsedValue], normal_count: int, total: int
) -> str:
    if not total:
        return (
            "No structured laboratory values could be read from this document. "
            "It may be a scan of poor quality, or a report type without a "
            "results table. Your doctor can review the original document."
        )
    if not abnormal:
        return (
            "All the values that could be read fall within the reference ranges "
            "printed on the report itself. Reference ranges vary between "
            "laboratories, so results are always interpreted against the range "
            "shown on your own report, alongside your symptoms and history."
        )

    sentences: list[str] = []
    for value in abnormal[:3]:
        analyte = ANALYTE_LOOKUP.get(value.normalised_name or "")
        plain = analyte.plain_name if analyte else value.test_name
        direction = "below" if value.flag == ResultFlag.LOW else "above"
        if value.flag == ResultFlag.CRITICAL:
            direction = "well outside"
        sentences.append(
            f"Your {plain} is {value.result_text}, which is {direction} the "
            f"reference range shown on the report"
            + (f" ({value.reference_range_text})." if value.reference_range_text else ".")
        )

    sentences.append(
        "A result outside the reference range can have several different "
        "causes and does not by itself confirm a diagnosis. These findings "
        "should be interpreted by a clinician together with your symptoms and "
        "medical history."
    )
    return " ".join(sentences)


def _suggest_specialty(abnormal: list[ParsedValue]) -> str:
    """Route on the specialties implied by the abnormal analytes."""
    if not abnormal:
        return "general_medicine"

    scores: dict[str, float] = {}
    for value in abnormal:
        analyte = ANALYTE_LOOKUP.get(value.normalised_name or "")
        if not analyte:
            continue
        weight = 2.0 if value.flag == ResultFlag.CRITICAL else 1.0
        scores[analyte.specialty] = scores.get(analyte.specialty, 0.0) + weight

    if not scores:
        return "general_medicine"
    return max(scores.items(), key=lambda kv: kv[1])[0]
