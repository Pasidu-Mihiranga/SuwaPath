"""What the report reader will and will not present as a clinical result.

Why this suite exists
---------------------
A patient uploaded a Google Form design document to the report reader. It was
read correctly — the text layer was extracted exactly — and then two lines of
it were presented as laboratory results:

    (Use Google Forms "Linear scale" labeled 1 = Strongly Disagree,   5   1-5   normal
    clicks, would save me meaningful time. Scale 1-                   5   1-2   CRITICAL

A survey's Likert scale is indistinguishable from a reference interval if the
only thing you check is whether a numeric range appears on the line. The row
rule accepted anything with `a printed range, or a unit, or a catalogue
match`, and "Scale 1-5" satisfied the first clause on its own. Nothing else
about the line had to resemble a measurement.

The extraction was never the problem, and nothing here is hardcoded: PyMuPDF
reads a digital text layer, Tesseract reads scans and photographs. Both are
exercised below. What was missing was any judgement about whether the text
that came back was a report at all.

The two failures to keep apart
------------------------------
Tightening this has a failure mode in each direction, and only one of them is
visible:

  * accepting prose as a result — loud and wrong, as above;
  * dropping a genuine abnormal value — silent, and more dangerous.

The second is easy to cause here. The analyte catalogue holds 22 entries, and
`_parse_inline` does not extract a trailing "%" as a unit, so a rule of
"needs a unit or a catalogue match" quietly discards an out-of-range
haematocrit. That case is pinned below precisely because it is the one a
future tightening would break without anyone noticing.

Needs no server and no database.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.ocr import (  # noqa: E402
    extract_document,
    looks_like_lab_report,
    parse_values,
    summarise,
)

SAMPLES = ROOT / "storage" / "samples"

# Every bundled report, through both reading paths. The scans go through
# Tesseract and the PDFs through the PyMuPDF text layer, so a change that
# works only for clean digital text fails here.
REAL_REPORTS = {
    "cbc_report.pdf": ("application/pdf", 6),
    "thyroid_profile.pdf": ("application/pdf", 4),
    "lipid_glucose.pdf": ("application/pdf", 7),
    "cbc_report_scan.png": ("image/png", 6),
    "thyroid_profile_scan.png": ("image/png", 4),
    "lipid_glucose_scan.png": ("image/png", 7),
}

# Text that is not a report but contains numbers and ranges. The first is the
# shape of the document that prompted this.
NOT_REPORTS = {
    "a survey with a Likert scale": (
        'Please rate the following. (Use Google Forms "Linear scale"\n'
        "labeled 1 = Strongly Disagree, 5 = Strongly Agree)\n"
        "Being able to book a doctor in a few clicks, would save me "
        "meaningful time. Scale 1-5   5   1-2\n"
    ),
    "a feedback form with short labels": (
        "Overall rating   4   1-5\nEase of use   2   1-5\n"
    ),
    "a school report": (
        "Mathematics   78   40-100\nScience   45   40-100\n"
    ),
    "a payslip": (
        "Basic salary   85000   50000-100000\nAllowance   12   0-15\n"
    ),
}


def real_reports_still_parse() -> list[str]:
    """The loud direction: real results must survive unchanged."""
    failures: list[str] = []
    for name, (mime, expected_rows) in REAL_REPORTS.items():
        path = SAMPLES / name
        if not path.is_file():
            continue
        result = extract_document(path, mime)
        if result.not_a_report_reason:
            failures.append(f"{name} was rejected as not a report")
            continue
        if len(result.values) != expected_rows:
            failures.append(
                f"{name} parsed {len(result.values)} rows, expected {expected_rows}"
            )
        if not any(
            str(v.flag) in ("low", "high", "critical") for v in result.values
        ):
            failures.append(f"{name} found no abnormal value at all")
    return failures


def document_metadata_is_not_a_result() -> list[str]:
    """"Age / Sex : 32" and "Collected On : 14/07/2026" are not tests.

    They have short, plausible-looking labels, so a name-shape check alone
    readmits them. What separates them is that no reference range is printed
    beside them.
    """
    failures: list[str] = []
    for name, (mime, _) in REAL_REPORTS.items():
        path = SAMPLES / name
        if not path.is_file():
            continue
        for value in extract_document(path, mime).values:
            if not value.normalised_name and not value.unit:
                failures.append(
                    f"{name} listed {value.test_name!r} as a result"
                )
    return failures


def prose_is_not_parsed_as_results() -> list[str]:
    """The specific failure that prompted this suite."""
    failures: list[str] = []
    for label, text in NOT_REPORTS.items():
        values, _ = parse_values(text)
        # Either the rows never parse, or the document gate discards them.
        # Both are acceptable; presenting them is not.
        if values and looks_like_lab_report(text, values):
            failures.append(
                f"{label} would be shown as {len(values)} clinical result(s): "
                f"{[v.test_name for v in values]}"
            )
    return failures


def unitless_analytes_survive() -> list[str]:
    """The silent direction: a real abnormal value must not be dropped.

    Haematocrit is not in the analyte catalogue and its "%" is not extracted
    as a unit, so it is exactly the row a stricter rule discards by accident.
    """
    failures: list[str] = []
    text = "Reference Range\nHaematocrit   38 %   40-50\n"
    values, _ = parse_values(text)
    match = [v for v in values if "haematocrit" in v.test_name.lower()]
    if not match:
        failures.append("an out-of-range haematocrit was dropped entirely")
    elif str(match[0].flag) not in ("low", "high", "critical"):
        failures.append(
            f"an out-of-range haematocrit was not flagged (got {match[0].flag})"
        )
    return failures


def non_reports_make_no_clinical_claim() -> list[str]:
    """A non-report must not produce a specialty or an in-range reassurance."""
    failures: list[str] = []
    text = NOT_REPORTS["a survey with a Likert scale"]
    values, _ = parse_values(text)
    if looks_like_lab_report(text, values):
        return ["the survey text was accepted as a lab report"]

    # Build the same shape `extract_document` would produce for it.
    from app.services.ocr import ExtractionResult

    result = ExtractionResult(
        engine="pymupdf-textlayer",
        raw_text=text,
        page_count=1,
        ocr_confidence=0.99,
        not_a_report_reason="not a report",
    )
    summary = summarise(result)
    if summary["suggested_specialty_code"] is not None:
        failures.append(
            f"a non-report suggested the specialty "
            f"{summary['suggested_specialty_code']!r}"
        )
    if "within the reference ranges" in summary["possible_next_step"]:
        failures.append(
            "a non-report told the patient their values were all in range"
        )
    return failures


def both_reading_engines_are_exercised() -> list[str]:
    """Guard the claim that OCR genuinely runs, rather than only text layers."""
    engines = set()
    for name, (mime, _) in REAL_REPORTS.items():
        path = SAMPLES / name
        if path.is_file():
            engines.add(extract_document(path, mime).engine)
    missing = {"pymupdf-textlayer", "tesseract"} - engines
    return [f"never exercised: {', '.join(sorted(missing))}"] if missing else []


def stacked_pdf_table_layout_parses() -> list[str]:
    """PDFs whose text layer emits one table cell per line must still parse."""
    failures: list[str] = []
    text = (
        "CLINICAL LABORATORY REPORT\nPatient ID:\n"
        "e4b29c9a-1d54-4a22-9e8c-8d1a3c6f4b9dDate Collected:\n2026-08-30\n"
        "Test Parameter\nResult\nUnit\nReference Range\nFlag\n"
        "White Blood Cell (WBC)  7.2  10³/µL  4.5 - 11.0  Normal\n"
        "Hemoglobin (Hb)  14.8  g/dL  13.5 - 17.5  Normal\n"
        "Platelet Count  245  10³/µL  150 - 450  Normal\n"
    )
    values, _ = parse_values(text)
    if any("e4b29c9a" in v.test_name for v in values):
        failures.append("patient UUID was parsed as a test name")
    names = {v.test_name.lower() for v in values}
    for expected in ("white blood cell (wbc)", "hemoglobin (hb)", "platelet count"):
        if expected not in names:
            failures.append(f"missing expected row {expected!r}")
    return failures


CHECKS = (
    ("Real reports still parse", real_reports_still_parse),
    ("Document metadata is not listed as a result", document_metadata_is_not_a_result),
    ("Prose is not parsed as clinical results", prose_is_not_parsed_as_results),
    ("Unitless analytes are not dropped", unitless_analytes_survive),
    ("Non-reports make no clinical claim", non_reports_make_no_clinical_claim),
    ("Both reading engines are exercised", both_reading_engines_are_exercised),
    ("Stacked PDF table rows parse", stacked_pdf_table_layout_parses),
)


def main() -> int:
    if not (SAMPLES / "cbc_report.pdf").is_file():
        print(f"Sample reports missing from {SAMPLES}.")
        return 1

    print("Medical report parsing\n")
    failures: list[str] = []
    for title, check in CHECKS:
        problems = check()
        print(f"  [{'ok' if not problems else 'FAIL'}] {title}")
        for problem in problems:
            print(f"         {problem}")
        failures.extend(problems)

    print(f"\n  Failed: {len(failures)}")
    return 1 if failures else 0


def test_real_reports_still_parse() -> None:
    assert real_reports_still_parse() == []


def test_document_metadata_is_not_a_result() -> None:
    assert document_metadata_is_not_a_result() == []


def test_prose_is_not_parsed_as_results() -> None:
    assert prose_is_not_parsed_as_results() == []


def test_unitless_analytes_survive() -> None:
    assert unitless_analytes_survive() == []


def test_non_reports_make_no_clinical_claim() -> None:
    assert non_reports_make_no_clinical_claim() == []


def test_both_reading_engines_are_exercised() -> None:
    assert both_reading_engines_are_exercised() == []


def test_stacked_pdf_table_layout_parses() -> None:
    assert stacked_pdf_table_layout_parses() == []


if __name__ == "__main__":
    raise SystemExit(main())
