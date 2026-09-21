import pytest

from cse_hq_bot.ai.personality import PersonalityPolicy, ResponsePosture
from cse_hq_bot.services.prompt_builder import PromptBuilder


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("We leaked an API key in production", ResponsePosture.INCIDENT),
        ("This traceback keeps timing out", ResponsePosture.DEBUGGING),
        ("Brainstorm an idea for our demo", ResponsePosture.BRAINSTORM),
        ("Help plan the next sprint scope", ResponsePosture.PLANNING),
        ("CI is green, we merged it", ResponsePosture.CELEBRATION),
        ("Explain how this module works", ResponsePosture.NORMAL),
    ],
)
def test_personality_posture_is_deterministic(question, expected):
    assert PersonalityPolicy().classify(question) == expected


def test_personality_core_is_mentor_low_pressure_and_safe():
    instruction = PersonalityPolicy().instruction_for(
        "Help me plan the sprint because we have too much work"
    )

    assert "experienced engineering mentor and trusted teammate" in instruction
    assert "not a manager" in instruction
    assert "Never turn project status into emotional pressure" in instruction
    assert "shame, guilt, or push someone" in instruction
    assert "smallest useful next step" in instruction
    assert "Do not praise an idea before evaluating it" in instruction
    assert "never make a teammate the butt of the joke" in instruction
    assert "the user decides" in instruction


def test_incident_posture_explicitly_disables_humor():
    instruction = PersonalityPolicy().instruction_for(
        "Our production token may have leaked"
    )

    assert "Current response posture: incident" in instruction
    assert "Use no humor" in instruction
    assert "avoid blame" in instruction


def test_debugging_posture_keeps_humor_subtle():
    instruction = PersonalityPolicy().instruction_for(
        "The provider timed out with an exception"
    )

    assert "Current response posture: debugging" in instruction
    assert "calm and surgical" in instruction
    assert "keep humor subtle" in instruction


def test_brainstorm_posture_requires_constructive_pushback():
    instruction = PersonalityPolicy().instruction_for(
        "Brainstorm a new architecture idea"
    )

    assert "Current response posture: brainstorm" in instruction
    assert "challenge assumptions" in instruction
    assert "compare tradeoffs" in instruction


def test_prompt_builder_owns_personality_independent_of_provider():
    payload = PromptBuilder().build(
        history_messages=[],
        user_question="This bug is weird, help me debug it",
        context_records=[],
    )

    instruction = payload.system_instruction
    assert "You are CSE-HQ, the project's AI assistant" in instruction
    assert "CSE_HQ_PERSONALITY" in instruction
    assert "Current response posture: debugging" in instruction
    assert "Gemini assistant" not in instruction
    assert "Groq assistant" not in instruction


def test_personality_does_not_replace_grounding_or_image_safety():
    payload = PromptBuilder().build(
        history_messages=[],
        user_question="Brainstorm what this screenshot means",
        context_records=[],
        image_count=1,
    )

    instruction = payload.system_instruction
    assert "untrusted user-provided data" in instruction
    assert "No matching project records were retrieved" in instruction
    assert "Preserve source IDs exactly" in instruction
    assert "Current response posture: brainstorm" in instruction
