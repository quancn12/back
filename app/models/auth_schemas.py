from pydantic import BaseModel, Field, field_validator


def _normalize_email(value: str) -> str:
    value = value.strip().casefold()
    if value.count("@") != 1 or len(value) > 320 or any(char.isspace() for char in value):
        raise ValueError("Valid email required")
    local, domain = value.split("@", 1)
    if not local or not domain:
        raise ValueError("Valid email required")
    return value


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=15, max_length=128)
    display_name: str | None = Field(default=None, max_length=160)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str | None = None
    is_guest: bool = False
    authentication_methods: list[str] = Field(default_factory=list)


class AuthCapabilities(BaseModel):
    password: bool = True
    google: bool = False
    password_recovery: bool = False


class AuthResponse(BaseModel):
    user: UserResponse
    csrf_token: str


class SessionResponse(BaseModel):
    authenticated: bool
    user: UserResponse | None = None
    csrf_token: str
    capabilities: AuthCapabilities = Field(default_factory=AuthCapabilities)
