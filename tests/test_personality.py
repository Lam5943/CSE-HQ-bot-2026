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


def test_prompt_language_policy_defaults_vietnamese_for_vietnamese_and_mixed_chat():
    payload = PromptBuilder().build(
        history_messages=[
            {"role": "assistant", "content": "这是之前的回答。"},
            {"role": "user", "content": "旧消息"},
        ],
        user_question="bro giải thích API này giúp mình với",
        context_records=[],
    )

    instruction = payload.system_instruction
    assert "<LANGUAGE_POLICY>" in instruction
    assert "current user message has priority over conversation history" in instruction
    assert "mixes Vietnamese with English" in instruction
    assert "default to Vietnamese" in instruction
    assert "Do not switch to Chinese" in instruction


def test_prompt_language_policy_allows_explicit_language_request():
    payload = PromptBuilder().build(
        history_messages=[],
        user_question="Please answer this in Chinese",
        context_records=[],
    )

    instruction = payload.system_instruction
    assert "unless the user writes in that language or explicitly asks for it" in instruction


def test_adaptive_style_detects_casual_bro_followup():
    policy = PersonalityPolicy()
    style = policy.infer_style(
        "pros and cons của cái này đi bro",
        history_messages=[],
    )

    assert style.register == "casual"
    assert style.address_hint == "bro"
    assert style.compact is True
    assert style.structured is False


def test_adaptive_style_uses_recent_user_history_for_short_followup():
    policy = PersonalityPolicy()
    style = policy.infer_style(
        "cái này thì sao?",
        history_messages=[
            {"role": "user", "content": "bro phân tích topic này giúp mình"},
            {"role": "assistant", "content": "Đây là phân tích trước."},
        ],
    )

    assert style.register == "casual"
    assert style.address_hint == "bro"
    assert style.compact is True


def test_adaptive_style_respects_formal_current_message():
    style = PersonalityPolicy().infer_style(
        "Vui lòng trình bày chi tiết theo từng phần.",
        history_messages=[{"role": "user", "content": "bro check cái này"}],
    )

    assert style.register == "formal"
    assert style.structured is True


def test_prompt_prefers_conversation_over_report_for_casual_followup():
    payload = PromptBuilder().build(
        history_messages=[
            {"role": "user", "content": "bro phân tích topic này đi"},
            {"role": "assistant", "content": "Mình phân tích rồi nè."},
        ],
        user_question="bro đưa mình pros and cons nha",
        context_records=[],
    )

    instruction = payload.system_instruction
    assert "You may call them 'bro' occasionally" in instruction
    assert "Answer the follow-up immediately" in instruction
    assert "Avoid turning ordinary chat into a report" in instruction or (
        "formal report-style sectioning only when the user asks for it" in instruction
    )
    assert "For general questions, brainstorming, or image analysis, do not announce" in instruction
