import uuid

from pydantic import BaseModel, EmailStr, Field


class RegisterFamilyRequest(BaseModel):
    family_name: str
    admin_display_name: str
    admin_email: EmailStr
    admin_password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ChildPinLoginRequest(BaseModel):
    family_id: uuid.UUID
    user_id: uuid.UUID
    pin: str


class PinLoginRequest(ChildPinLoginRequest):
    pass


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8)
