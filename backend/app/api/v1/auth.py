"""Authentication, registration and the current-user profile."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_patient_profile
from app.core.db import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models.enums import Language, Sex, UserRole
from app.models.identity import AuditLog, PatientProfile, User
from app.models.providers import Doctor

router = APIRouter(prefix="/auth", tags=["auth"])


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=160)
    role: UserRole = UserRole.PATIENT
    phone: str | None = None
    preferred_language: Language = Language.EN
    date_of_birth: date | None = None
    sex: Sex | None = None
    city: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    preferred_language: str
    phone: str | None = None
    avatar_url: str | None = None
    hospital_id: str | None = None
    # Populated for patients so the client can render the dashboard in one call.
    patient_profile: dict | None = None
    doctor_profile: dict | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


TokenResponse.model_rebuild()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def serialise_user(db: Session, user: User) -> UserResponse:
    patient_profile = None
    doctor_profile = None

    if str(user.role) == str(UserRole.PATIENT):
        profile = get_patient_profile(db, user.id)
        if profile:
            patient_profile = {
                "id": profile.id,
                "age": profile.age,
                "date_of_birth": profile.date_of_birth.isoformat() if profile.date_of_birth else None,
                "sex": str(profile.sex) if profile.sex else None,
                "blood_group": profile.blood_group,
                "city": profile.city,
                "district": profile.district,
                "latitude": profile.latitude,
                "longitude": profile.longitude,
                "chronic_conditions": profile.chronic_conditions or [],
                "allergies": profile.allergies or [],
                "current_medications": profile.current_medications or [],
                "is_pregnant": profile.is_pregnant,
                "expected_delivery_date": (
                    profile.expected_delivery_date.isoformat()
                    if profile.expected_delivery_date
                    else None
                ),
                "accessibility_large_text": profile.accessibility_large_text,
            }

    elif str(user.role) == str(UserRole.DOCTOR):
        doctor = db.execute(
            select(Doctor).where(Doctor.user_id == user.id)
        ).scalar_one_or_none()
        if doctor:
            doctor_profile = {
                "id": doctor.id,
                "specialty_code": doctor.specialty.code if doctor.specialty else None,
                "specialty_name": doctor.specialty.name if doctor.specialty else None,
                "sub_specialty": doctor.sub_specialty,
                "hospital_id": doctor.hospital_id,
                "hospital_name": doctor.hospital.name if doctor.hospital else None,
                "years_experience": doctor.years_experience,
                "languages": doctor.languages or [],
                "verification_status": str(doctor.verification_status),
                "consultation_fee_lkr": doctor.consultation_fee_lkr,
            }

    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=str(user.role),
        preferred_language=str(user.preferred_language),
        phone=user.phone,
        avatar_url=user.avatar_url,
        hospital_id=user.hospital_id,
        patient_profile=patient_profile,
        doctor_profile=doctor_profile,
    )


def _issue_tokens(db: Session, user: User) -> TokenResponse:
    user.last_login_at = datetime.now(timezone.utc)
    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_role=str(user.role),
            action="auth.login",
            resource_type="user",
            resource_id=user.id,
        )
    )
    db.commit()
    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        user=serialise_user(db, user),
    )


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    existing = db.execute(
        select(User).where(User.email == payload.email.lower())
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    # Self-registration is limited to patients and guardians; clinical and
    # administrative accounts are provisioned by an administrator.
    if payload.role not in (UserRole.PATIENT, UserRole.GUARDIAN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Doctor, hospital and system accounts must be created by a "
                "SuwaPath administrator."
            ),
        )

    user = User(
        email=payload.email.lower(),
        phone=payload.phone,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        role=payload.role,
        preferred_language=payload.preferred_language,
    )
    db.add(user)
    db.flush()

    if payload.role == UserRole.PATIENT:
        db.add(
            PatientProfile(
                user_id=user.id,
                date_of_birth=payload.date_of_birth,
                sex=payload.sex,
                city=payload.city,
                chronic_conditions=[],
                allergies=[],
                current_medications=[],
            )
        )
    db.commit()
    return _issue_tokens(db, user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.execute(
        select(User).where(User.email == payload.email.lower())
    ).scalar_one_or_none()

    # Same message for unknown email and wrong password, so the endpoint does
    # not confirm which addresses are registered.
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )
    return _issue_tokens(db, user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    claims = decode_token(payload.refresh_token)
    if claims.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token."
        )
    user = db.get(User, claims.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer active."
        )
    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        user=serialise_user(db, user),
    )


@router.get("/me", response_model=UserResponse)
def me(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> UserResponse:
    return serialise_user(db, current_user)


class UpdateProfileRequest(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    preferred_language: Language | None = None
    city: str | None = None
    district: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    blood_group: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    chronic_conditions: list[str] | None = None
    allergies: list[str] | None = None
    current_medications: list[str] | None = None
    is_pregnant: bool | None = None
    expected_delivery_date: date | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    accessibility_large_text: bool | None = None


@router.patch("/me", response_model=UserResponse)
def update_me(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserResponse:
    data = payload.model_dump(exclude_unset=True)

    for field in ("full_name", "phone", "preferred_language"):
        if field in data and data[field] is not None:
            setattr(current_user, field, data[field])

    profile_fields = {
        k: v for k, v in data.items()
        if k not in ("full_name", "phone", "preferred_language")
    }
    if profile_fields and str(current_user.role) == str(UserRole.PATIENT):
        profile = get_patient_profile(db, current_user.id)
        if profile is None:
            profile = PatientProfile(user_id=current_user.id)
            db.add(profile)
        for field, value in profile_fields.items():
            if value is not None:
                setattr(profile, field, value)

    db.commit()
    return serialise_user(db, current_user)
