from types import SimpleNamespace

import pytest

from src.tools.confirmation_guard import appointment_mutation_block_response
from src.tools.context import ToolExecutionContext


TOOL = "bulutses_panel_create_appointment_with_doctor"
PARAMS = {
    "starts_at": "2026-10-31T10:00",
    "doctor": "Alim Hoca",
    "patient_confirmed": "yes",
}


def guard(user_input, history, parameters=None, tool_name=TOOL):
    return appointment_mutation_block_response(
        tool_name,
        PARAMS if parameters is None else parameters,
        user_input=user_input,
        conversation_history=history,
    )


def test_identity_confirmation_cannot_authorize_appointment_creation():
    result = guard(
        "Evet, benim.",
        [{"role": "assistant", "content": "Yalçın Kaz ile mi görüşüyorum?"}],
    )

    assert result is not None
    assert result["status"] == "blocked"
    assert result["reason"] == "identity_confirmation_is_not_transaction_confirmation"


@pytest.mark.parametrize(
    "reply",
    [
        "Evet.",
        "Tamam.",
        "Olur.",
        "Uygundur.",
        "Onaylıyorum.",
        "Kabul ediyorum.",
        "Tabii ki.",
        "Elbette.",
        "Peki, yapalım.",
        "Oluşturabilirsiniz.",
    ],
)
def test_natural_turkish_affirmations_allow_confirmed_readback(reply):
    result = guard(
        reply,
        [
            {
                "role": "assistant",
                "content": (
                    "31 Ekim Cumartesi saat 10.00 için Alim Hoca ile "
                    "randevunuzu oluşturmamı onaylıyor musunuz?"
                ),
            }
        ],
    )

    assert result is None


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("Hayır.", "caller_did_not_affirm"),
        ("Evet ama saat 11 olsun.", "caller_did_not_affirm"),
        ("Beni duyuyor musunuz?", "caller_did_not_affirm"),
    ],
)
def test_negative_ambiguous_or_changed_answer_is_blocked(reply, reason):
    result = guard(
        reply,
        [
            {
                "role": "assistant",
                "content": "31 Ekim saat 10.00 randevusunu oluşturmamı onaylıyor musunuz?",
            }
        ],
    )

    assert result is not None
    assert result["reason"] == reason


def test_prompt_without_date_or_time_is_blocked():
    result = guard(
        "Tamam.",
        [{"role": "assistant", "content": "Randevuyu oluşturmamı onaylıyor musunuz?"}],
    )

    assert result is not None
    assert result["reason"] == "confirmation_prompt_missing_schedule_details"


def test_model_parameter_cannot_override_missing_confirmation():
    result = guard(
        "Tamam.",
        [
            {
                "role": "assistant",
                "content": "31 Ekim saat 10.00 randevusunu oluşturmamı onaylıyor musunuz?",
            }
        ],
        parameters={**PARAMS, "patient_confirmed": "no"},
    )

    assert result is not None
    assert result["reason"] == "tool_parameter_not_confirmed"


def test_availability_lookup_is_not_affected():
    assert guard(
        "Cumartesi uygun mu?",
        [],
        tool_name="bulutses_panel_check_availability",
    ) is None


def test_latest_user_can_be_derived_from_persisted_history():
    history = [
        {
            "role": "assistant",
            "content": "31 Ekim saat 10.00 randevusunu oluşturmamı onaylıyor musunuz?",
        },
        {"role": "user", "content": "Olur."},
    ]

    assert guard(None, history) is None


class _SessionStore:
    def __init__(self, history):
        self.session = SimpleNamespace(
            conversation_history=history,
            no_input_state={},
            current_action={},
        )

    async def get_by_call_id(self, _call_id):
        return self.session


@pytest.mark.asyncio
async def test_execution_context_blocks_before_tool_dispatch():
    context = ToolExecutionContext(
        call_id="synthetic-call",
        provider_name="pipeline",
        user_input="Evet, benim.",
        prior_conversation_history=[
            {"role": "assistant", "content": "Yalçın Kaz ile mi görüşüyorum?"}
        ],
        session_store=_SessionStore([]),
    )

    result = await context.get_tool_block_response(TOOL, PARAMS)

    assert result is not None
    assert result["code"] == "explicit_appointment_confirmation_required"

