"""SuwaPath system administration: users, providers, catalogues, AI config."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.security import get_current_user, hash_password, require_system_admin
from app.models.care import Appointment, CareEnrollment, CareProgramme, Consultation
from app.models.clinical import MedicalDocument, MedicalImage, StructuredIntake
from app.models.enums import Language, UserRole, VerificationStatus
from app.models.identity import AuditLog, SystemConfig, User
from app.models.providers import (
    DiagnosticTest,
    Doctor,
    FacilityCapability,
    Hospital,
    Specialty,
)
from app.services.ai_orchestrator import orchestrator_status
from app.services.graph import graph_topology
from app.services.knowledge import knowledge_service
from app.services.vision import list_adapters

router = APIRouter(prefix="/admin", tags=["system-admin"])


@router.get("/overview")
def overview(
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Platform-wide counts and health."""

    def count(model) -> int:
        return db.execute(select(func.count()).select_from(model)).scalar() or 0

    role_rows = db.execute(
        select(User.role, func.count()).group_by(User.role)
    ).all()

    verification_rows = db.execute(
        select(Doctor.verification_status, func.count()).group_by(
            Doctor.verification_status
        )
    ).all()

    return {
        "users": {
            "total": count(User),
            "by_role": {str(role): total for role, total in role_rows},
        },
        "providers": {
            "doctors": count(Doctor),
            "hospitals": db.execute(
                select(func.count()).select_from(Hospital).where(
                    Hospital.facility_type == "hospital"
                )
            ).scalar() or 0,
            "diagnostic_centres": db.execute(
                select(func.count()).select_from(Hospital).where(
                    Hospital.facility_type == "diagnostic_centre"
                )
            ).scalar() or 0,
            "verification": {
                str(status_value): total for status_value, total in verification_rows
            },
        },
        "activity": {
            "appointments": count(Appointment),
            "consultations": count(Consultation),
            "symptom_intakes": count(StructuredIntake),
            "documents": count(MedicalDocument),
            "images": count(MedicalImage),
            "care_enrollments": count(CareEnrollment),
        },
        "catalogues": {
            "specialties": count(Specialty),
            "diagnostic_tests": count(DiagnosticTest),
            "care_programmes": count(CareProgramme),
        },
    }


@router.get("/ai-config")
def ai_config(
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Live AI subsystem status: orchestrator, graph, retrieval, CV adapters."""
    configs = db.execute(select(SystemConfig)).scalars().all()
    return {
        "orchestrator": orchestrator_status(),
        "graph": graph_topology(),
        "knowledge_retrieval": {
            "backend": knowledge_service.backend,
            "collection": "suwapath_health_knowledge",
        },
        "vision_adapters": list_adapters(),
        "stored_config": [
            {
                "key": c.key,
                "category": c.category,
                "description": c.description,
                "value": c.value,
            }
            for c in configs
        ],
    }


class ConfigUpdateRequest(BaseModel):
    value: dict


@router.put("/config/{key}")
def update_config(
    key: str,
    payload: ConfigUpdateRequest,
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict:
    config = db.execute(
        select(SystemConfig).where(SystemConfig.key == key)
    ).scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="Configuration key not found.")

    config.value = payload.value
    db.add(
        AuditLog(
            actor_user_id=current_user.id,
            actor_role=str(current_user.role),
            action="config.update",
            resource_type="system_config",
            resource_id=config.id,
            detail={"key": key},
        )
    )
    db.commit()
    return {"key": config.key, "value": config.value}


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------
@router.get("/users")
def list_users(
    role: UserRole | None = None,
    search: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(User)
    if role:
        stmt = stmt.where(User.role == str(role))
    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            func.lower(User.full_name).like(pattern) | func.lower(User.email).like(pattern)
        )

    total = db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar() or 0
    rows = db.execute(
        stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()

    return {
        "total": total,
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "role": str(u.role),
                "is_active": u.is_active,
                "hospital_id": u.hospital_id,
                "last_login_at": u.last_login_at,
                "created_at": u.created_at,
            }
            for u in rows
        ],
    }


class CreateStaffRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str
    role: UserRole
    hospital_id: str | None = None
    # Doctor-only fields
    specialty_code: str | None = None
    years_experience: int = 0
    languages: list[str] = ["en"]
    consultation_fee_lkr: float = 3500.0


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_staff_user(
    payload: CreateStaffRequest,
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Provision a doctor, hospital admin or system admin account."""
    if payload.role in (UserRole.PATIENT, UserRole.GUARDIAN):
        raise HTTPException(
            status_code=400,
            detail="Patients and guardians register themselves.",
        )
    if db.execute(
        select(User).where(User.email == payload.email.lower())
    ).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered.")

    user = User(
        email=payload.email.lower(),
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        hospital_id=payload.hospital_id,
        preferred_language=Language.EN,
    )
    db.add(user)
    db.flush()

    if payload.role == UserRole.DOCTOR:
        if not payload.specialty_code:
            raise HTTPException(
                status_code=400, detail="A doctor account requires a specialty."
            )
        specialty = db.execute(
            select(Specialty).where(Specialty.code == payload.specialty_code)
        ).scalar_one_or_none()
        if specialty is None:
            raise HTTPException(status_code=404, detail="Specialty not found.")
        db.add(
            Doctor(
                user_id=user.id,
                hospital_id=payload.hospital_id,
                specialty_id=specialty.id,
                years_experience=payload.years_experience,
                languages=payload.languages,
                consultation_fee_lkr=payload.consultation_fee_lkr,
                # New provider records start unverified (spec §2, role 5).
                verification_status=VerificationStatus.PENDING,
            )
        )

    db.add(
        AuditLog(
            actor_user_id=current_user.id,
            actor_role=str(current_user.role),
            action="user.create",
            resource_type="user",
            resource_id=user.id,
            detail={"role": str(payload.role)},
        )
    )
    db.commit()
    return {"id": user.id, "email": user.email, "role": str(user.role)}


class ToggleActiveRequest(BaseModel):
    is_active: bool


@router.patch("/users/{user_id}/active")
def set_user_active(
    user_id: str,
    payload: ToggleActiveRequest,
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.id == current_user.id:
        raise HTTPException(
            status_code=400, detail="You cannot deactivate your own account."
        )

    user.is_active = payload.is_active
    db.add(
        AuditLog(
            actor_user_id=current_user.id,
            actor_role=str(current_user.role),
            action="user.set_active",
            resource_type="user",
            resource_id=user.id,
            detail={"is_active": payload.is_active},
        )
    )
    db.commit()
    return {"id": user.id, "is_active": user.is_active}


# --------------------------------------------------------------------------
# Provider verification
# --------------------------------------------------------------------------
@router.get("/providers/pending")
def pending_providers(
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    doctors = db.execute(
        select(Doctor)
        .options(selectinload(Doctor.user), selectinload(Doctor.specialty), selectinload(Doctor.hospital))
        .where(Doctor.verification_status != str(VerificationStatus.VERIFIED))
    ).scalars().unique().all()

    return [
        {
            "doctor_id": d.id,
            "name": d.user.full_name if d.user else None,
            "email": d.user.email if d.user else None,
            "specialty_name": d.specialty.name if d.specialty else None,
            "hospital_name": d.hospital.name if d.hospital else None,
            "slmc_registration_no": d.slmc_registration_no,
            "years_experience": d.years_experience,
            "verification_status": str(d.verification_status),
            "created_at": d.created_at,
        }
        for d in doctors
    ]


class VerifyRequest(BaseModel):
    verification_status: VerificationStatus
    note: str | None = None


@router.patch("/providers/{doctor_id}/verify")
def verify_provider(
    doctor_id: str,
    payload: VerifyRequest,
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict:
    doctor = db.get(Doctor, doctor_id)
    if doctor is None:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    doctor.verification_status = payload.verification_status
    db.add(
        AuditLog(
            actor_user_id=current_user.id,
            actor_role=str(current_user.role),
            action="provider.verify",
            resource_type="doctor",
            resource_id=doctor.id,
            detail={"status": str(payload.verification_status), "note": payload.note},
        )
    )
    db.commit()
    return {
        "doctor_id": doctor.id,
        "verification_status": str(doctor.verification_status),
    }


# --------------------------------------------------------------------------
# Facilities and catalogues
# --------------------------------------------------------------------------
@router.get("/hospitals")
def list_hospitals(
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    hospitals = db.execute(
        select(Hospital).options(selectinload(Hospital.capabilities))
    ).scalars().unique().all()
    return [
        {
            "id": h.id,
            "name": h.name,
            "facility_type": str(h.facility_type),
            "city": h.city,
            "district": h.district,
            "has_emergency": h.has_emergency,
            "is_24_hours": h.is_24_hours,
            "bed_count": h.bed_count,
            "verification_status": str(h.verification_status),
            "is_active": h.is_active,
            "capability_count": len([c for c in h.capabilities if c.is_available]),
        }
        for h in hospitals
    ]


class CapabilityToggleRequest(BaseModel):
    capability_code: str
    is_available: bool


@router.patch("/hospitals/{hospital_id}/capabilities")
def toggle_capability(
    hospital_id: str,
    payload: CapabilityToggleRequest,
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict:
    from app.clinical.catalog import CAPABILITY_BY_CODE

    hospital = db.get(Hospital, hospital_id)
    if hospital is None:
        raise HTTPException(status_code=404, detail="Facility not found.")

    definition = CAPABILITY_BY_CODE.get(payload.capability_code)
    if definition is None:
        raise HTTPException(status_code=400, detail="Unknown capability code.")

    capability = db.execute(
        select(FacilityCapability).where(
            FacilityCapability.hospital_id == hospital_id,
            FacilityCapability.capability_code == payload.capability_code,
        )
    ).scalar_one_or_none()

    if capability is None:
        capability = FacilityCapability(
            hospital_id=hospital_id,
            capability_code=definition.code,
            capability_name=definition.name,
            category=definition.category,
        )
        db.add(capability)

    capability.is_available = payload.is_available
    db.commit()
    return {
        "hospital_id": hospital_id,
        "capability_code": payload.capability_code,
        "is_available": payload.is_available,
    }


@router.get("/audit-logs")
def audit_logs(
    action: str | None = None,
    limit: int = Query(default=100, le=500),
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)

    rows = db.execute(
        stmt.order_by(AuditLog.created_at.desc()).limit(limit)
    ).scalars().all()

    out = []
    for log in rows:
        actor = db.get(User, log.actor_user_id) if log.actor_user_id else None
        out.append(
            {
                "id": log.id,
                "action": log.action,
                "actor_name": actor.full_name if actor else None,
                "actor_role": log.actor_role,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "detail": log.detail,
                "created_at": log.created_at,
            }
        )
    return out
