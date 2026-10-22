"""Fail-closed confirmation checks for appointment mutations.

Model-supplied boolean fields are not proof that the caller approved a write.
This module verifies the actual dialog immediately preceding the tool call.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Mapping, Optional


_APPOINTMENT_MUTATION_RE = re.compile(
    r"(?:create|book|schedule|reschedule|cancel|delete|update)[_-]?appointment|"
    r"appointment[_-]?(?:create|book|schedule|reschedule|cancel|delete|update)",
    re.IGNORECASE,
)

_IDENTITY_PROMPT_MARKERS = (
    "ile mi görüşüyorum",
    "siz misiniz",
    "adınız",
    "isminiz",
    "kimliğinizi",
    "kimlik teyidi",
    "identity",
    "is this",
    "am i speaking with",
)

_CONFIRMATION_PROMPT_MARKERS = (
    "onaylıyor musunuz",
    "onaylar mısınız",
    "onayınızı",
    "oluşturayım mı",
    "oluşturmamı",
    "planlayayım mı",
    "planlamamı",
    "değiştireyim mi",
    "değiştirmemi",
    "iptal edeyim mi",
    "iptal etmemi",
    "kabul ediyor musunuz",
    "uygun mudur",
    "uygun mu",
    "do you confirm",
    "would you like me to",
    "shall i",
    "please confirm",
)

_SCHEDULE_DETAIL_RE = re.compile(
    r"(?:\b\d{1,2}[.:]\d{2}\b|\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b|"
    r"\b(?:bugün|yarın|öbür gün|pazartesi|salı|çarşamba|perşembe|cuma|cumartesi|pazar|"
    r"ocak|şubat|mart|nisan|mayıs|haziran|temmuz|ağustos|eylül|ekim|kasım|aralık)\b|"
    r"\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b)",
    re.IGNORECASE,
)

_NEGATIVE_OR_CHANGE_RE = re.compile(
    r"\b(?:hayır|istemiyorum|olmasın|vazgeçtim|değil|iptal|ama|fakat|yalnız|ancak|"
    r"no|don't|do not|not|instead|but)\b",
    re.IGNORECASE,
)

_AFFIRMATIVE_RE = re.compile(
    r"\b(?:evet|tamam(?:dır)?|olur|uygun(?:dur)?|onaylıyorum|onaylıyoruz|"
    r"kabul ediyorum|tabii(?: ki)?|elbette|peki|yapalım|oluşturun|"
    r"oluşturabilirsiniz|planlayın|kesinleştirin|yes|okay|ok|confirmed?|"
    r"go ahead|please do)\b",
    re.IGNORECASE,
)

_TRUE_CONFIRMATION_VALUES = {
    "1",
    "true",
    "yes",
    "evet",
    "confirmed",
    "onay",
    "onaylıyorum",
}


def _normalized_text(value: Any) -> str:
    text = str(value or "").strip().replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"\s+", " ", text)


def _latest_assistant_before_user(
    history: Iterable[Mapping[str, Any]],
    user_input: str,
) -> str:
    messages = list(history or [])
    normalized_user = _normalized_text(user_input)
    boundary = len(messages)

    if normalized_user:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if str(message.get("role") or "").lower() != "user":
                continue
            if _normalized_text(message.get("content")) == normalized_user:
                boundary = index
                break

    for index in range(boundary - 1, -1, -1):
        message = messages[index]
        if str(message.get("role") or "").lower() != "assistant":
            continue
        content = _normalized_text(message.get("content"))
        if content:
            return content
    return ""


def _parameter_confirms(parameters: Mapping[str, Any]) -> bool:
    value = parameters.get("patient_confirmed")
    if isinstance(value, bool):
        return value
    return _normalized_text(value) in _TRUE_CONFIRMATION_VALUES


def _caller_affirmed(user_input: str) -> bool:
    normalized = _normalized_text(user_input)
    if not normalized or len(normalized.split()) > 12:
        return False
    if _NEGATIVE_OR_CHANGE_RE.search(normalized):
        return False
    return bool(_AFFIRMATIVE_RE.search(normalized))


def appointment_mutation_block_response(
    tool_name: Optional[str],
    parameters: Optional[Mapping[str, Any]],
    *,
    user_input: Optional[str],
    conversation_history: Iterable[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return a blocking result when an appointment write lacks dialog evidence.

    The guard is intentionally narrow: it applies only to appointment mutation
    tools that carry the ``patient_confirmed`` contract. Availability and lookup
    tools remain unaffected.
    """

    normalized_name = _normalized_text(tool_name).replace("-", "_")
    args: Mapping[str, Any] = parameters or {}
    if "patient_confirmed" not in args or not _APPOINTMENT_MUTATION_RE.search(normalized_name):
        return None

    history = list(conversation_history or [])
    current_user = _normalized_text(user_input)
    if not current_user:
        for message in reversed(history):
            if str(message.get("role") or "").lower() == "user":
                current_user = _normalized_text(message.get("content"))
                if current_user:
                    break
    previous_assistant = _latest_assistant_before_user(
        history,
        current_user,
    )

    reason = ""
    if not _parameter_confirms(args):
        reason = "tool_parameter_not_confirmed"
    elif not _caller_affirmed(current_user):
        reason = "caller_did_not_affirm"
    elif any(marker in previous_assistant for marker in _IDENTITY_PROMPT_MARKERS):
        reason = "identity_confirmation_is_not_transaction_confirmation"
    elif not any(marker in previous_assistant for marker in _CONFIRMATION_PROMPT_MARKERS):
        reason = "no_explicit_appointment_confirmation_prompt"
    elif not _SCHEDULE_DETAIL_RE.search(previous_assistant):
        reason = "confirmation_prompt_missing_schedule_details"

    if not reason:
        return None

    return {
        "status": "blocked",
        "code": "explicit_appointment_confirmation_required",
        "reason": reason,
        "message": (
            "No appointment action was performed. Do not report success. "
            "Read back the exact appointment date, time, doctor and requested action, "
            "then ask for confirmation and wait for the caller's answer in a new turn. "
            "Identity confirmation is never appointment confirmation."
        ),
    }

