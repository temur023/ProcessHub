"""The universal library contract; bank approval is never inferred from upload."""
import json
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_BUNDLE_FILES = 10
MAX_BUNDLE_BYTES = 40 * 1024 * 1024
MAX_METADATA_BYTES = 32 * 1024
DOCUMENT_TYPES = (
    ('job_instruction', 'Должностная инструкция', 'instructions'),
    ('department_regulation', 'Положение о подразделении', 'regulations'),
    ('procedure', 'Порядок / регламент', 'procedures'),
    ('methodology', 'Методика', 'procedures'),
    ('order', 'Приказ / распоряжение / решение', 'orders'),
    ('form', 'Приложение / форма / шаблон', 'forms'),
    ('organization', 'Организационные материалы', 'organization'),
    ('sop', 'Рабочее описание / SOP', 'work'),
    ('analytics', 'Аналитические материалы', 'analytics'),
    ('correspondence', 'Сопроводительные материалы', 'correspondence'),
    ('other', 'На классификации', 'other'),
)
DocumentType = Literal['job_instruction', 'department_regulation', 'procedure', 'methodology',
                       'order', 'form', 'organization', 'sop', 'analytics', 'correspondence', 'other']


def clean_text(value):
    value = ' '.join(value.split())
    if not value or any(ord(char) < 32 or ord(char) == 127 or 0xD800<=ord(char)<=0xDFFF for char in value):
        raise ValueError('Укажите непустой текст')
    return value


def bounded_metadata(value):
    def depth(item, level=0):
        if level > 10:
            raise ValueError('Слишком много вложенных реквизитов')
        if isinstance(item, dict):
            if any(not isinstance(key, str) or len(key)>300 or '\x00' in key for key in item):
                raise ValueError('Некорректное название реквизита')
            for child in item.values():
                depth(child, level+1)
        elif isinstance(item, list):
            for child in item:
                depth(child, level+1)
        elif isinstance(item,str) and ('\x00' in item or any(0xD800<=ord(char)<=0xDFFF for char in item)):
            raise ValueError('Некорректный символ в реквизитах')
    depth(value)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > MAX_METADATA_BYTES:
        raise ValueError('Реквизиты не должны превышать 32 КБ')
    return value


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ApplicabilityInput(StrictInput):
    department_id: UUID
    status: Literal['unconfirmed', 'confirmed'] = 'unconfirmed'
    evidence: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def evidence_required(self):
        self.evidence = ' '.join(self.evidence.split())
        if self.evidence:
            clean_text(self.evidence)
        if self.status == 'confirmed' and not self.evidence:
            raise ValueError('Подтверждённая применимость требует основания')
        return self


class NewPositionInput(StrictInput):
    """A position named while filing an instruction; reused if it already exists."""
    display_name: str = Field(min_length=1, max_length=200)
    unit_name: str = Field(min_length=1, max_length=300)
    category: Literal['management', 'specialists', 'support'] = 'specialists'
    _text = field_validator('display_name', 'unit_name')(clean_text)

    @field_validator('unit_name')
    @classmethod
    def unit_path(cls, value):
        # Nested services are written "Служба / Группа"; keep that separator exact.
        return ' / '.join(part.strip() for part in value.split('/') if part.strip())


class DocumentInput(StrictInput):
    title: str = Field(min_length=1, max_length=300)
    document_type: DocumentType = 'other'
    library_scope: Literal['department', 'bank'] = 'department'
    department_id: UUID | None = None
    owner_department_id: UUID | None = None
    role_id: UUID | None = None
    bank_number: str | None = Field(default=None, max_length=100)
    document_date: date | None = None
    metadata: dict = Field(default_factory=dict)
    applicability: list[ApplicabilityInput] = Field(default_factory=list, max_length=100)
    new_position: NewPositionInput | None = None

    _title = field_validator('title')(clean_text)
    _metadata = field_validator('metadata')(bounded_metadata)

    @model_validator(mode='after')
    def scope_and_role(self):
        if (self.library_scope == 'department') != (self.department_id is not None):
            raise ValueError('Выберите департамент для области департамента; общая область не имеет папки департамента')
        if (self.role_id is not None or self.new_position is not None) and (
                self.library_scope != 'department' or self.document_type != 'job_instruction'):
            raise ValueError('Должность указывается только для инструкции в департаменте')
        if self.role_id is not None and self.new_position is not None:
            raise ValueError('Выберите существующую должность или создайте новую')
        if len({item.department_id for item in self.applicability}) != len(self.applicability):
            raise ValueError('Повторный департамент в применимости')
        if self.bank_number is not None:
            self.bank_number = clean_text(self.bank_number) if self.bank_number.strip() else None
        return self


class DocumentPatch(StrictInput):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    document_type: DocumentType | None = None
    owner_department_id: UUID | None = None
    role_id: UUID | None = None
    bank_number: str | None = Field(default=None, max_length=100)
    document_date: date | None = None
    metadata: dict | None = None
    applicability: list[ApplicabilityInput] | None = Field(default=None, max_length=100)
    new_position: NewPositionInput | None = None

    @field_validator('title')
    @classmethod
    def title_text(cls, value):
        return clean_text(value) if value is not None else value

    @field_validator('metadata')
    @classmethod
    def metadata_size(cls, value):
        return bounded_metadata(value) if value is not None else value

    @model_validator(mode='after')
    def no_required_null(self):
        for name in ('title', 'document_type', 'metadata', 'applicability'):
            if name in self.model_fields_set and getattr(self,name) is None:
                raise ValueError('Обязательное поле не может быть пустым')
        return self


class FileMetadata(StrictInput):
    purpose: Literal['main', 'attachment', 'approval', 'supplement'] = 'main'
    source_metadata: dict = Field(default_factory=dict)
    _metadata = field_validator('source_metadata')(bounded_metadata)


class EditionInput(StrictInput):
    label: str | None = Field(default=None, max_length=200)
    metadata: dict = Field(default_factory=dict)
    _metadata = field_validator('metadata')(bounded_metadata)

    @field_validator('label')
    @classmethod
    def label_text(cls,value):
        return clean_text(value) if value and value.strip() else None


class VerificationInput(StrictInput):
    valid_from: date
    valid_to: date | None = None
    # The bank's adoption date is an independent documentary fact. An act
    # may schedule future effect or specify retrospective effect; neither
    # its date nor the validity period may be inferred from upload time.
    approved_on: date | None = None
    evidence: str = Field(min_length=1, max_length=2000)
    # A new edition usually replaces the open period of the previous one.
    # The previous period then ends the day before, as a separate fact.
    close_previous: bool = False
    _evidence = field_validator('evidence')(clean_text)

    @model_validator(mode='after')
    def valid_period(self):
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError('Дата окончания предшествует началу действия')
        return self


class EndValidityInput(StrictInput):
    valid_to: date
    evidence: str = Field(min_length=1,max_length=2000)
    _evidence = field_validator('evidence')(clean_text)
