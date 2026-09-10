from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib
METHOD_CAPS={"VERIFIED_VENDOR":1.0,"DIRECT_SOURCE":.95,"PUBLIC_SOURCE":.90,"DATABASE":.85,"RULE":.75,"MODEL_INFERENCE":.55,"PATTERN_GUESS":.30,"UNKNOWN":0.0}
@dataclass
class Value:
    value: object=None; method: str="UNKNOWN"; confidence: float=0.0; source: str|None=None
    timestamp: str=field(default_factory=lambda:datetime.now(timezone.utc).isoformat()); note: str|None=None
    def __post_init__(self): self.confidence=min(max(self.confidence,0),METHOD_CAPS.get(self.method,0))
@dataclass
class Contact:
    contact_id:str; first_name:Value; last_name:Value; title:Value; department:Value; email:Value; phone:Value; professional_profile:Value
    lawful_basis:str="NOT_ESTABLISHED"; review_level:str="GREEN"
    @property
    def email_hash(self):
        return hashlib.sha256(self.email.value.lower().strip().encode()).hexdigest() if self.email.value else None
@dataclass
class Prospect:
    prospect_id:str; company:dict; contacts:list=field(default_factory=list); tenders:list=field(default_factory=list); news:list=field(default_factory=list); triggers:list=field(default_factory=list)
    scoring:dict=field(default_factory=dict); moving_intelligence:dict=field(default_factory=dict); commercial_intelligence:dict=field(default_factory=dict)
    why_now:str=""; recommended_action:str=""; confidence:float=0.0; sources:list=field(default_factory=list)
    duplicate_status:str="CLEAR"; duplicate_evidence:list=field(default_factory=list); human_review:bool=False; review_level:str="GREEN"
    lawful_basis:str="NOT_ESTABLISHED"; refresh_due_at:str|None=None; run_id:str|None=None; cost_usd:float=0.0; schema_version:str="2.0"
    def to_dict(self): return asdict(self)
def merge_value(existing,incoming,audit,field_name,force=False):
    if incoming.value is None:return existing
    if existing.value is None or incoming.confidence>=existing.confidence or force:
        if existing.value is not None and incoming.confidence<existing.confidence:audit.append({"event":"FIELD_OVERWRITE_FORCED","field":field_name})
        return incoming
    audit.append({"event":"FIELD_OVERWRITE_REJECTED","field":field_name,"old_confidence":existing.confidence,"new_confidence":incoming.confidence}); return existing
