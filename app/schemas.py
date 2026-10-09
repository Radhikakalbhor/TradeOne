from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any

class ErrorResponse(BaseModel):
    code: str
    message: str
    ref: str

class PurposeSchema(BaseModel):
    code: str
    text: str

class DataRangeSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    to: str

class FetchFrequencySchema(BaseModel):
    unit: str = "DAY"
    value: int = 4

class ConsentCreateRequest(BaseModel):
    purpose: PurposeSchema
    fiTypes: List[str]
    dataRange: DataRangeSchema
    consentDurationDays: int = 90
    fetchFrequency: FetchFrequencySchema
    customerEmail: str
    redirectUrl: str
    webhookUrl: str

class ConsentCreateResponse(BaseModel):
    consentHandle: str
    status: str
    approvalUrl: str

class ConsentDetailResponse(BaseModel):
    consentHandle: str
    consentId: Optional[str] = None
    status: str
    artefact: Optional[Dict[str, Any]] = None
    signature: Optional[str] = None

class DataSessionCreateRequest(BaseModel):
    consentId: str
    dataRange: Optional[Dict[str, str]] = None

class DataSessionCreateResponse(BaseModel):
    sessionId: str
    status: str

class IngestHoldingsRequest(BaseModel):
    email: str
    dpName: str
    dpId: str
    maskedAccNumber: str
    isin: str
    quantityDelta: float
    avgPrice: float = 0.0
    reason: str = "BUY_SETTLEMENT"

class NomineeUpdateRequest(BaseModel):
    name: str
    relationship_type: str
    percentage: int
    dob: Optional[str] = None
    guardian_name: Optional[str] = None

class ProvisionUserRequest(BaseModel):
    email: str
    full_name: Optional[str] = None

class InternalEventRequest(BaseModel):
    provider: str
    email: str
    event: str = "HOLDINGS_CHANGED"
    occurredAt: Optional[str] = None
