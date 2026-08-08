"""Medical document and medical image upload, processing and navigation.

Both pipelines end the same way: a `Recommendation` row produced by the shared
care-navigation engine, so a report result and an image finding lead to
provider matching exactly like a symptom conversation does (internal rules 4
and 5).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import build_patient_context, resolve_patient_access
from app.core.config import settings
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.clinical import (
    ExtractedReport,
    ExtractedValue,
    ImageAnalysis,
    MedicalDocument,
    MedicalImage,
    Recommendation,
)
from app.models.enums import (
    DocumentType,
    GuardianPermissionType,
    ImageModality,
    NotificationCategory,
    NotificationPriority,
    ProcessingStatus,
    ResultFlag,
    UrgencyLevel,
    UserRole,
)
from app.models.identity import User
from app.models.platform import Notification
from app.models.providers import Specialty
from app.services import ocr as ocr_service
from app.services.navigation import navigate
from app.services.red_flag_engine import assess_concepts, build_context
from app.services.vision import ImageValidationError, UnsupportedModalityError, list_adapters, screen_image

router = APIRouter(tags=["medical-records"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_DOCUMENT_TYPES = {"application/pdf", "image/jpeg", "image/jpg", "image/png"}
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png"}


def _save_upload(upload: UploadFile, directory: Path) -> tuple[Path, int]:
    data = upload.file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    suffix = Path(upload.filename or "").suffix.lower() or ".bin"
    stored = directory / f"{uuid.uuid4().hex}{suffix}"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(data)
    return stored, len(data)


def _create_recommendation(
    db: Session,
    *,
    patient_user_id: str,
    specialty_code: str,
    reason: str,
    source: str,
    capabilities: list[str],
    urgency: UrgencyLevel,
    next_action: str,
    confidence: float,
    document_id: str | None = None,
    image_id: str | None = None,
    tests: list[dict] | None = None,
    guidance: str | None = None,
) -> Recommendation:
    specialty = db.execute(
        select(Specialty).where(Specialty.code == specialty_code)
    ).scalar_one_or_none()

    recommendation = Recommendation(
        patient_user_id=patient_user_id,
        document_id=document_id,
        image_id=image_id,
        source=source,
        care_category=(
            "Urgent medical assessment"
            if urgency in (UrgencyLevel.EMERGENCY, UrgencyLevel.URGENT)
            else "Scheduled specialist consultation"
        ),
        specialty_id=specialty.id if specialty else None,
        specialty_code=specialty_code,
        secondary_specialty_codes=[],
        urgency=urgency,
        reason=reason,
        suggested_next_action=next_action,
        confidence=confidence,
        required_capabilities=capabilities,
        recommended_tests=tests or [],
        patient_guidance=guidance,
    )
    db.add(recommendation)
    db.flush()
    return recommendation


# ==========================================================================
# Documents
# ==========================================================================
@router.post("/documents", status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile = File(...),
    document_type: DocumentType = Form(default=DocumentType.LAB_REPORT),
    patient_user_id: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Upload a report, run OCR, explain it and route to care."""
    if file.content_type not in ALLOWED_DOCUMENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Upload a PDF, JPG or PNG file.",
        )

    target_id = patient_user_id or current_user.id
    if target_id != current_user.id:
        resolve_patient_access(
            db, current_user, target_id, permission=GuardianPermissionType.REPORTS
        )

    stored_path, size = _save_upload(file, settings.document_dir)

    document = MedicalDocument(
        patient_user_id=target_id,
        uploaded_by_user_id=current_user.id,
        file_name=file.filename or stored_path.name,
        stored_path=str(stored_path),
        mime_type=file.content_type,
        file_size_bytes=size,
        document_type=document_type,
        processing_status=ProcessingStatus.PROCESSING,
    )
    db.add(document)
    db.flush()

    try:
        result = ocr_service.extract_document(stored_path, file.content_type)
        summary = ocr_service.summarise(result)
    except Exception as exc:  # noqa: BLE001
        document.processing_status = ProcessingStatus.FAILED
        document.processing_error = str(exc)[:500]
        db.commit()
        raise HTTPException(
            status_code=422,
            detail=f"Could not read this document: {exc}",
        ) from exc

    report = ExtractedReport(
        document_id=document.id,
        ocr_engine=result.engine,
        raw_text=result.raw_text[:60000],
        ocr_confidence=result.ocr_confidence,
        page_count=result.page_count,
        facility_name=result.facility_name,
        patient_name_on_report=result.patient_name,
        collection_date=result.collection_date,
        report_title=result.report_title,
        tables=result.tables,
        summary=summary["summary"],
        plain_language_explanation=summary["explanation"],
        key_findings=summary["key_findings"],
        suggested_specialty_code=summary["suggested_specialty_code"],
        possible_next_step=summary["possible_next_step"],
    )
    db.add(report)
    db.flush()

    for value in result.values:
        db.add(
            ExtractedValue(
                report_id=report.id,
                test_name=value.test_name,
                normalised_name=value.normalised_name,
                result_text=value.result_text,
                result_numeric=value.result_numeric,
                unit=value.unit,
                reference_range_text=value.reference_range_text,
                reference_low=value.reference_low,
                reference_high=value.reference_high,
                reference_source=value.reference_source,
                flag=value.flag,
                row_index=value.row_index,
            )
        )

    document.processing_status = ProcessingStatus.COMPLETED
    document.document_type = result.document_type

    # Feed the finding into shared care navigation (internal rule 5).
    has_critical = any(v.flag == ResultFlag.CRITICAL for v in result.values)
    urgency = UrgencyLevel.URGENT if has_critical else (
        UrgencyLevel.ROUTINE if summary["abnormal_count"] else UrgencyLevel.SELF_CARE
    )
    context = build_patient_context(db, target_id)
    assessment = assess_concepts(set(), build_context(**{
        k: v for k, v in context.items()
        if k in ("age", "sex", "is_pregnant", "is_postpartum", "pregnancy_week", "chronic_conditions")
    }))
    assessment.urgency = urgency
    from app.services.red_flag_engine import ESCALATION_MESSAGES

    assessment.escalation_message = ESCALATION_MESSAGES[urgency]

    navigation = navigate(
        red_flags=assessment,
        chief_complaint=report.report_title or "Medical report",
        specialty_override=summary["suggested_specialty_code"],
        override_reason=summary["explanation"],
        extra_capabilities=["laboratory"],
        source="report",
    )
    recommendation = _create_recommendation(
        db,
        patient_user_id=target_id,
        specialty_code=navigation.specialty_code,
        reason=summary["explanation"],
        source="report",
        capabilities=navigation.required_capabilities,
        urgency=urgency,
        next_action=summary["possible_next_step"],
        confidence=0.75 if summary["abnormal_count"] else 0.6,
        document_id=document.id,
        tests=navigation.recommended_tests,
        guidance=assessment.escalation_message,
    )

    db.add(
        Notification(
            user_id=target_id,
            category=NotificationCategory.REPORT,
            priority=(
                NotificationPriority.HIGH if has_critical else NotificationPriority.NORMAL
            ),
            title="Your report has been processed",
            body=summary["summary"][:400],
            action_type="document",
            action_id=document.id,
        )
    )
    db.commit()

    return _document_dict(db, document, report, recommendation)


def _document_dict(
    db: Session,
    document: MedicalDocument,
    report: ExtractedReport | None,
    recommendation: Recommendation | None = None,
) -> dict:
    values = (
        db.execute(
            select(ExtractedValue)
            .where(ExtractedValue.report_id == report.id)
            .order_by(ExtractedValue.row_index)
        ).scalars().all()
        if report
        else []
    )

    return {
        "id": document.id,
        "file_name": document.file_name,
        "document_type": str(document.document_type),
        "processing_status": str(document.processing_status),
        "processing_error": document.processing_error,
        "uploaded_at": document.created_at,
        "file_size_bytes": document.file_size_bytes,
        "extracted": (
            {
                "ocr_engine": report.ocr_engine,
                "ocr_confidence": report.ocr_confidence,
                "page_count": report.page_count,
                "report_title": report.report_title,
                "facility_name": report.facility_name,
                "collection_date": report.collection_date,
                "summary": report.summary,
                "plain_language_explanation": report.plain_language_explanation,
                "key_findings": report.key_findings,
                "possible_next_step": report.possible_next_step,
                "suggested_specialty_code": report.suggested_specialty_code,
                # Table rows keep their original order (spec §11).
                "tables": report.tables,
                "values": [
                    {
                        "test_name": v.test_name,
                        "result": v.result_text,
                        "unit": v.unit,
                        "reference_range": v.reference_range_text,
                        "reference_source": v.reference_source,
                        "flag": str(v.flag),
                        "row_index": v.row_index,
                    }
                    for v in values
                ],
            }
            if report
            else None
        ),
        "recommendation": (
            {
                "id": recommendation.id,
                "specialty_code": recommendation.specialty_code,
                "urgency": str(recommendation.urgency),
                "reason": recommendation.reason,
                "suggested_next_action": recommendation.suggested_next_action,
                "confidence": recommendation.confidence,
                "required_capabilities": recommendation.required_capabilities,
                "recommended_tests": recommendation.recommended_tests,
            }
            if recommendation
            else None
        ),
    }


@router.get("/documents")
def list_documents(
    patient_user_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    target_id = patient_user_id or current_user.id
    if target_id != current_user.id:
        resolve_patient_access(
            db, current_user, target_id, permission=GuardianPermissionType.REPORTS
        )

    documents = db.execute(
        select(MedicalDocument)
        .options(selectinload(MedicalDocument.extracted))
        .where(MedicalDocument.patient_user_id == target_id)
        .order_by(MedicalDocument.created_at.desc())
    ).scalars().unique()

    out = []
    for document in documents:
        report = document.extracted
        out.append(
            {
                "id": document.id,
                "file_name": document.file_name,
                "document_type": str(document.document_type),
                "processing_status": str(document.processing_status),
                "uploaded_at": document.created_at,
                "summary": report.summary if report else None,
                "abnormal_count": len(report.key_findings) if report else 0,
                "suggested_specialty_code": (
                    report.suggested_specialty_code if report else None
                ),
            }
        )
    return out


@router.get("/documents/{document_id}")
def get_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    document = db.execute(
        select(MedicalDocument)
        .options(selectinload(MedicalDocument.extracted))
        .where(MedicalDocument.id == document_id)
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    if document.patient_user_id != current_user.id:
        resolve_patient_access(
            db, current_user, document.patient_user_id,
            permission=GuardianPermissionType.REPORTS,
        )

    recommendation = db.execute(
        select(Recommendation).where(Recommendation.document_id == document.id)
    ).scalar_one_or_none()
    return _document_dict(db, document, document.extracted, recommendation)


@router.get("/documents/{document_id}/file")
def download_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    document = db.get(MedicalDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if document.patient_user_id != current_user.id:
        resolve_patient_access(
            db, current_user, document.patient_user_id,
            permission=GuardianPermissionType.REPORTS,
        )

    path = Path(document.stored_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Stored file is missing.")
    return FileResponse(path, media_type=document.mime_type, filename=document.file_name)


# ==========================================================================
# Images
# ==========================================================================
@router.post("/images", status_code=status.HTTP_201_CREATED)
def upload_image(
    file: UploadFile = File(...),
    modality: ImageModality = Form(default=ImageModality.CHEST_XRAY),
    patient_user_id: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Upload a medical image, screen it and route to care."""
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Upload a JPG or PNG image.")

    target_id = patient_user_id or current_user.id
    if target_id != current_user.id:
        resolve_patient_access(
            db, current_user, target_id, permission=GuardianPermissionType.REPORTS
        )

    stored_path, size = _save_upload(file, settings.image_dir)

    image = MedicalImage(
        patient_user_id=target_id,
        uploaded_by_user_id=current_user.id,
        file_name=file.filename or stored_path.name,
        stored_path=str(stored_path),
        mime_type=file.content_type,
        file_size_bytes=size,
        modality=modality,
        processing_status=ProcessingStatus.PROCESSING,
    )
    db.add(image)
    db.flush()

    heatmap_name = f"{Path(stored_path).stem}_heatmap.png"
    try:
        result = screen_image(stored_path, modality, heatmap_name=heatmap_name)
    except ImageValidationError as exc:
        image.processing_status = ProcessingStatus.FAILED
        image.validation_passed = False
        image.validation_notes = str(exc)
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnsupportedModalityError as exc:
        image.processing_status = ProcessingStatus.FAILED
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    analysis = ImageAnalysis(
        image_id=image.id,
        adapter_name=result.adapter_name,
        model_name=result.model_name,
        model_version=result.model_version,
        finding_label=result.finding_label,
        finding_description=result.finding_description,
        confidence=result.confidence,
        class_probabilities=result.class_probabilities,
        is_uncertain=result.is_uncertain,
        uncertainty_note=result.uncertainty_note,
        heatmap_path=result.heatmap_path,
        has_visual_explanation=bool(result.heatmap_path),
        suggested_specialty_code=result.suggested_specialty_code,
        suggested_next_step=result.suggested_next_step,
        inference_ms=result.inference_ms,
    )
    db.add(analysis)
    image.processing_status = ProcessingStatus.COMPLETED
    image.validation_passed = True

    # An abnormal screening result raises urgency; an uncertain one does not.
    positive = result.class_probabilities.get("pneumonia", 0.0) > 0.5
    urgency = (
        UrgencyLevel.URGENT
        if positive and not result.is_uncertain
        else UrgencyLevel.ROUTINE
    )
    context = build_patient_context(db, target_id)
    assessment = assess_concepts(set(), build_context(**{
        k: v for k, v in context.items()
        if k in ("age", "sex", "is_pregnant", "is_postpartum", "pregnancy_week", "chronic_conditions")
    }))
    assessment.urgency = urgency
    from app.services.red_flag_engine import ESCALATION_MESSAGES

    assessment.escalation_message = ESCALATION_MESSAGES[urgency]

    navigation = navigate(
        red_flags=assessment,
        chief_complaint=result.finding_label,
        specialty_override=result.suggested_specialty_code,
        override_reason=result.finding_description,
        extra_capabilities=result.required_capabilities,
        source="image",
    )
    recommendation = _create_recommendation(
        db,
        patient_user_id=target_id,
        specialty_code=navigation.specialty_code,
        reason=result.finding_description,
        source="image",
        capabilities=navigation.required_capabilities,
        urgency=urgency,
        next_action=result.suggested_next_step or navigation.suggested_next_action,
        confidence=result.confidence,
        image_id=image.id,
        tests=navigation.recommended_tests,
        guidance=assessment.escalation_message,
    )

    db.add(
        Notification(
            user_id=target_id,
            category=NotificationCategory.REPORT,
            priority=(
                NotificationPriority.HIGH if positive else NotificationPriority.NORMAL
            ),
            title="Image screening complete",
            body=result.finding_label,
            action_type="image",
            action_id=image.id,
        )
    )
    db.commit()
    return _image_dict(image, analysis, recommendation)


def _image_dict(
    image: MedicalImage,
    analysis: ImageAnalysis | None,
    recommendation: Recommendation | None = None,
) -> dict:
    return {
        "id": image.id,
        "file_name": image.file_name,
        "modality": str(image.modality),
        "processing_status": str(image.processing_status),
        "validation_passed": image.validation_passed,
        "validation_notes": image.validation_notes,
        "uploaded_at": image.created_at,
        "analysis": (
            {
                "finding_label": analysis.finding_label,
                "finding_description": analysis.finding_description,
                "confidence": analysis.confidence,
                "class_probabilities": analysis.class_probabilities,
                "is_uncertain": analysis.is_uncertain,
                "uncertainty_note": analysis.uncertainty_note,
                "has_visual_explanation": analysis.has_visual_explanation,
                "heatmap_url": (
                    f"{settings.api_v1_prefix}/images/{image.id}/heatmap"
                    if analysis.has_visual_explanation
                    else None
                ),
                "adapter_name": analysis.adapter_name,
                "model_name": analysis.model_name,
                "model_version": analysis.model_version,
                "inference_ms": analysis.inference_ms,
                "suggested_specialty_code": analysis.suggested_specialty_code,
                "suggested_next_step": analysis.suggested_next_step,
                # Screening support, never a diagnosis (spec §12).
                "disclaimer": (
                    "AI screening support only. This is not a diagnosis and must "
                    "be reviewed by a qualified clinician."
                ),
            }
            if analysis
            else None
        ),
        "recommendation": (
            {
                "id": recommendation.id,
                "specialty_code": recommendation.specialty_code,
                "urgency": str(recommendation.urgency),
                "reason": recommendation.reason,
                "suggested_next_action": recommendation.suggested_next_action,
                "confidence": recommendation.confidence,
                "required_capabilities": recommendation.required_capabilities,
                "recommended_tests": recommendation.recommended_tests,
            }
            if recommendation
            else None
        ),
    }


@router.get("/images")
def list_images(
    patient_user_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    target_id = patient_user_id or current_user.id
    if target_id != current_user.id:
        resolve_patient_access(
            db, current_user, target_id, permission=GuardianPermissionType.REPORTS
        )

    images = db.execute(
        select(MedicalImage)
        .options(selectinload(MedicalImage.analysis))
        .where(MedicalImage.patient_user_id == target_id)
        .order_by(MedicalImage.created_at.desc())
    ).scalars().unique()
    return [_image_dict(i, i.analysis) for i in images]


@router.get("/images/{image_id}")
def get_image(
    image_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    image = db.execute(
        select(MedicalImage)
        .options(selectinload(MedicalImage.analysis))
        .where(MedicalImage.id == image_id)
    ).scalar_one_or_none()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    if image.patient_user_id != current_user.id:
        resolve_patient_access(
            db, current_user, image.patient_user_id,
            permission=GuardianPermissionType.REPORTS,
        )

    recommendation = db.execute(
        select(Recommendation).where(Recommendation.image_id == image.id)
    ).scalar_one_or_none()
    return _image_dict(image, image.analysis, recommendation)


@router.get("/images/{image_id}/file")
def download_image(
    image_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    image = db.get(MedicalImage, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    if image.patient_user_id != current_user.id:
        resolve_patient_access(
            db, current_user, image.patient_user_id,
            permission=GuardianPermissionType.REPORTS,
        )
    path = Path(image.stored_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Stored image is missing.")
    return FileResponse(path, media_type=image.mime_type, filename=image.file_name)


@router.get("/images/{image_id}/heatmap")
def image_heatmap(
    image_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    image = db.execute(
        select(MedicalImage)
        .options(selectinload(MedicalImage.analysis))
        .where(MedicalImage.id == image_id)
    ).scalar_one_or_none()
    if image is None or image.analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    if image.patient_user_id != current_user.id:
        resolve_patient_access(
            db, current_user, image.patient_user_id,
            permission=GuardianPermissionType.REPORTS,
        )

    path = Path(image.analysis.heatmap_path or "")
    if not path.exists():
        raise HTTPException(
            status_code=404, detail="No visual explanation is available for this image."
        )
    return FileResponse(path, media_type="image/png")


@router.get("/vision/adapters")
def vision_adapters(current_user: User = Depends(get_current_user)) -> list[dict]:
    """Registered CV model adapters and which one is currently active."""
    return list_adapters()
