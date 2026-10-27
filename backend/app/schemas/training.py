from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

class MemberIn(BaseModel):
    user_id: int
    role: Literal['hr', 'executive', 'facilitator', 'participant']
    department_id: int | None = None
    location: str = Field(default='', max_length=200)

class Question(BaseModel):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,49}$')
    prompt: str = Field(min_length=1, max_length=500)
    options: list[str] = Field(min_length=2, max_length=10)
    correct: int = Field(ge=0)
    outcome: str = Field(default='Knowledge', max_length=100)
    @model_validator(mode='after')
    def valid(self):
        if self.correct >= len(self.options):
            raise ValueError('Correct answer must be an option index')
        return self

class RevisionIn(BaseModel):
    curriculum: list[str] = Field(min_length=1, max_length=100)
    questions: list[Question] = Field(min_length=1, max_length=100)
    @model_validator(mode='after')
    def valid(self):
        if len({q.key for q in self.questions}) != len(self.questions):
            raise ValueError('Question keys must be unique')
        if any(not x.strip() or len(x) > 2000 for x in self.curriculum):
            raise ValueError('Curriculum modules must contain 1–2000 characters')
        return self

class ProgramIn(RevisionIn):
    details: dict = Field(default_factory=dict)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=4000)

class SessionIn(BaseModel):
    revision_id: int
    facilitator_id: int
    title: str = Field(min_length=1, max_length=200)
    location: str = Field(default='', max_length=200)
    department_id: int | None = None
    starts_at: datetime
    ends_at: datetime
    capacity: int = Field(default=100, ge=1, le=100000)
    @field_validator('starts_at', 'ends_at')
    @classmethod
    def utc(cls, value):
        if value.tzinfo is None:
            raise ValueError('Include a timezone in session dates')
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    @model_validator(mode='after')
    def valid(self):
        if self.ends_at <= self.starts_at:
            raise ValueError('Session end must follow start')
        return self

class RegistrationIn(BaseModel):
    user_id: int

class AttendanceIn(BaseModel):
    attendance: Literal['registered', 'present', 'late', 'absent', 'cancelled']

class CertificateIn(BaseModel):
    user_id: int
    name: str = Field(min_length=1, max_length=200)
    expires_at: datetime
    quality_notes: str = Field(default='', max_length=4000)
    @field_validator('expires_at')
    @classmethod
    def utc(cls, value):
        if value.tzinfo is None:
            raise ValueError('Include a timezone in certificate expiry')
        return value.astimezone(timezone.utc).replace(tzinfo=None)

class ResponseIn(BaseModel):
    answers: dict[str, int]
    confidence: int = Field(ge=1, le=5)
    feedback: str = Field(default='', max_length=4000)
    anonymous: bool = False

class TrainingIntentIn(BaseModel):
    code: str
    entities: dict = Field(default_factory=dict)
