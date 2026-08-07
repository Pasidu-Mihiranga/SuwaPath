"""Generates realistic sample medical reports for the demo.

Produces both a text-layer PDF (fast path) and a rasterised PNG of the same
report (forces the Tesseract OCR path), so both branches of the document
pipeline are demonstrable.
"""

from __future__ import annotations

from pathlib import Path

import fitz

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "storage" / "samples"


REPORTS: dict[str, list[str]] = {
    "cbc_report": [
        "LankaCare Central Hospital",
        "Department of Laboratory Medicine",
        "No. 148, Hospital Road, Colombo 07",
        "",
        "FULL BLOOD COUNT REPORT",
        "",
        "Patient Name : Nimali Fernando",
        "Age / Sex    : 32 Years / Female",
        "Sample No    : LC-2026-88214",
        "Collected On : 14/07/2026",
        "Reported On  : 14/07/2026",
        "",
        "Test                        Result        Unit          Reference Range",
        "--------------------------------------------------------------------------",
        "Haemoglobin                 10.2          g/dL          12.0 - 16.0",
        "Total WBC                   8200          /uL           4000 - 11000",
        "Platelet Count              245000        /uL           150000 - 450000",
        "Serum Ferritin              14            ng/mL         30 - 300",
        "Serum Iron                  42            ug/dL         60 - 170",
        "ESR                         28            mm/hr         < 20",
        "",
        "Comments: Please correlate clinically.",
        "",
        "Authorised by: Consultant Haematologist",
    ],
    "thyroid_profile": [
        "Serene Health Medical Centre",
        "Clinical Laboratory Services",
        "",
        "THYROID PROFILE",
        "",
        "Patient Name : Nimali Fernando",
        "Collected On : 20/07/2026",
        "",
        "Test                        Result        Unit          Reference Range",
        "--------------------------------------------------------------------------",
        "TSH                         6.42          mIU/L         0.40 - 4.00",
        "Free T4                     0.82          ng/dL         0.80 - 1.80",
        "Anti-TPO                    215           IU/mL         < 35",
        "Vitamin D (25-OH)           18.6          ng/mL         30 - 100",
        "",
        "Comments: Repeat in 6 weeks if treatment adjusted.",
    ],
    "lipid_glucose": [
        "PureLab Diagnostics - Colombo",
        "",
        "METABOLIC SCREENING PANEL",
        "",
        "Patient Name : Sunil Fernando",
        "Age / Sex    : 69 Years / Male",
        "Collected On : 02/08/2026",
        "",
        "Test                        Result        Unit          Reference Range",
        "--------------------------------------------------------------------------",
        "Fasting Blood Sugar         146           mg/dL         70 - 100",
        "HbA1c                       7.8           %             < 5.7",
        "Total Cholesterol           228           mg/dL         < 200",
        "LDL Cholesterol             152           mg/dL         < 100",
        "HDL Cholesterol             38            mg/dL         > 40",
        "Triglycerides               196           mg/dL         < 150",
        "Serum Creatinine            1.1           mg/dL         0.6 - 1.3",
        "",
        "Comments: Fasting sample. Clinical correlation advised.",
    ],
}


def _write_pdf(name: str, lines: list[str]) -> Path:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    path = SAMPLES_DIR / f"{name}.pdf"

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    y = 60.0
    for index, line in enumerate(lines):
        # Headings and the table header row are emphasised.
        is_heading = index < 5 and line.isupper()
        font = "hebo" if is_heading or line.startswith("Test ") else "helv"
        size = 13 if is_heading else 9.5
        page.insert_text((56, y), line, fontname=font, fontsize=size)
        y += 20 if is_heading else 14

    doc.save(path)
    doc.close()
    return path


def _write_png(name: str, pdf_path: Path) -> Path:
    path = SAMPLES_DIR / f"{name}_scan.png"
    doc = fitz.open(pdf_path)
    # 200 dpi mimics a phone photo / flatbed scan of the printed report.
    pixmap = doc[0].get_pixmap(dpi=200)
    pixmap.save(path)
    doc.close()
    return path


def generate_all() -> list[Path]:
    created: list[Path] = []
    for name, lines in REPORTS.items():
        pdf_path = _write_pdf(name, lines)
        created.append(pdf_path)
        created.append(_write_png(name, pdf_path))
    return created


if __name__ == "__main__":
    for created_path in generate_all():
        print(f"  wrote {created_path}")
