import re
from dataclasses import dataclass
from enum import StrEnum


class ResponsePosture(StrEnum):
    NORMAL = "normal"
    BRAINSTORM = "brainstorm"
    DEBUGGING = "debugging"
    INCIDENT = "incident"
    PLANNING = "planning"
    CELEBRATION = "celebration"


_INCIDENT_PATTERN = re.compile(
    r"\b(?:security|incident|breach|leak(?:ed)?|credential|api[ -]?key|token|"
    r"production down|prod down|outage|compromised|401|403)\b",
    re.IGNORECASE,
)
_DEBUG_PATTERN = re.compile(
    r"\b(?:error|bug|traceback|exception|fail(?:ed|ing)?|timeout|timed out|"
    r"debug|crash(?:ed)?|broken|not working|không hoạt động|lỗi)\b",
    re.IGNORECASE,
)
_BRAINSTORM_PATTERN = re.compile(
    r"\b(?:brainstorm|idea|ideas|concept|what if|should we|ý tưởng|nên làm|"
    r"recommend|proposal)\b",
    re.IGNORECASE,
)
_PLANNING_PATTERN = re.compile(
    r"\b(?:plan|planning|roadmap|sprint|scope|deadline|milestone|priorit(?:y|ize)|"
    r"kế hoạch|phạm vi|tiến độ)\b",
    re.IGNORECASE,
)
_CELEBRATION_PATTERN = re.compile(
    r"\b(?:done|fixed|green|passed|merged|shipped|success|"
    r"xong|chạy rồi|thành công)\b",
    re.IGNORECASE,
)

_CASUAL_PATTERN = re.compile(
    r"(?:\bbro(?:ther)?\b|\bbruh\b|\byo\b|\bey+y+\b|\bman\b|"
    r"\blol\b|\blmao\b|:sob:|😭|💀|=\)+|:v)",
    re.IGNORECASE,
)
_FORMAL_PATTERN = re.compile(
    r"\b(?:vui lòng|xin hãy|kính|anh/chị|please provide|could you please|"
    r"would you please)\b",
    re.IGNORECASE,
)
_STRUCTURED_REQUEST_PATTERN = re.compile(
    r"\b(?:report|báo cáo|chi tiết|detailed|comprehensive|full breakdown|"
    r"từng phần|step[- ]by[- ]step|theo mục|structured|cấu trúc)\b",
    re.IGNORECASE,
)
_SHORT_FOLLOWUP_PATTERN = re.compile(
    r"\b(?:cái này|cái đó|ý này|ý đó|vậy|thế|còn cái|pros and cons|"
    r"ưu nhược|so sánh|thì sao|what about|how about)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConversationStyle:
    register: str
    address_hint: str
    compact: bool
    structured: bool
    emoji_ok: bool



@dataclass(frozen=True)
class PersonalityPolicy:
    def infer_style(
        self,
        question: str,
        history_messages: list[dict] | None = None,
    ) -> ConversationStyle:
        recent_user_text = " ".join(
            str(message.get("content") or "")
            for message in (history_messages or [])[-6:]
            if message.get("role") == "user"
        )
        current = " ".join(str(question or "").split())
        style_text = f"{recent_user_text} {current}".strip()

        if _FORMAL_PATTERN.search(current):
            register = "formal"
        elif _CASUAL_PATTERN.search(style_text):
            register = "casual"
        else:
            register = "neutral"

        if register == "formal":
            address_hint = "neutral-formal"
        elif re.search(r"\bbro(?:ther)?\b|\bbruh\b", style_text, re.IGNORECASE):
            address_hint = "bro"
        elif re.search(r"\bmình\b", style_text, re.IGNORECASE):
            address_hint = "mình-bạn"
        else:
            address_hint = "neutral"

        compact = (
            len(current.split()) <= 18
            or bool(_SHORT_FOLLOWUP_PATTERN.search(current))
        )
        structured = bool(_STRUCTURED_REQUEST_PATTERN.search(current))
        emoji_ok = register != "formal" and bool(
            re.search(r"[😭💀😂🤣😅🥲]|:sob:|=\)+", style_text)
        )

        return ConversationStyle(
            register=register,
            address_hint=address_hint,
            compact=compact,
            structured=structured,
            emoji_ok=emoji_ok,
        )

    def classify(self, question: str) -> ResponsePosture:
        text = " ".join(question.strip().split())
        if _INCIDENT_PATTERN.search(text):
            return ResponsePosture.INCIDENT
        if _DEBUG_PATTERN.search(text):
            return ResponsePosture.DEBUGGING
        if _BRAINSTORM_PATTERN.search(text):
            return ResponsePosture.BRAINSTORM
        if _PLANNING_PATTERN.search(text):
            return ResponsePosture.PLANNING
        if _CELEBRATION_PATTERN.search(text):
            return ResponsePosture.CELEBRATION
        return ResponsePosture.NORMAL

    def instruction_for(
        self,
        question: str,
        history_messages: list[dict] | None = None,
    ) -> str:
        posture = self.classify(question)
        style = self.infer_style(question, history_messages)
        return (
            "<CSE_HQ_PERSONALITY>\n"
            "You are CSE-HQ: an experienced engineering mentor and trusted teammate, "
            "not a manager. Be practical, calm, concise-first, and willing to challenge "
            "weak assumptions. Diagnose situations without judging people. Offer options "
            "and tradeoffs instead of pressure or commands. Never use deadlines, rankings, "
            "workload, or another member's progress to shame, guilt, or push someone. "
            "Never turn project status into emotional pressure. When workload is heavy, "
            "help reduce scope, identify dependencies, or suggest the smallest useful next step. "
            "Do not praise an idea before evaluating it. Humor is welcome in small doses: joke "
            "about bugs, tools, CI, weird technical situations, or the project itself, never "
            "make a teammate the butt of the joke. Reduce humor when the user seems stressed "
            "and use none for security incidents or serious failures. Celebrate real progress "
            "naturally, without performative hype. CSE-HQ remembers; the model reasons; "
            "the user decides.\n"
            f"Current response posture: {posture.value}. {self._posture_instruction(posture)}\n"
            f"{self._style_instruction(style)}\n"
            "</CSE_HQ_PERSONALITY>"
        )

    @staticmethod
    def _style_instruction(style: ConversationStyle) -> str:
        address = {
            "bro": (
                "The user naturally uses 'bro'. You may call them 'bro' occasionally and "
                "refer to yourself naturally as 'mình', but do not force 'bro' into every sentence."
            ),
            "mình-bạn": (
                "Mirror a natural Vietnamese 'mình/bạn' relationship unless the current "
                "message clearly uses another form of address."
            ),
            "neutral-formal": (
                "Keep address neutral and polite; do not introduce slang the user did not use."
            ),
            "neutral": (
                "Use neutral, natural address and avoid inventing intimacy."
            ),
        }[style.address_hint]
        format_note = (
            "The user explicitly wants structure/detail, so headings or numbered sections are fine."
            if style.structured
            else (
                "This is a short or contextual follow-up. Answer the follow-up immediately, "
                "usually in a few compact paragraphs or a short bullet list. Do not restate "
                "the full previous analysis; expand only if the user asks."
                if style.compact
                else (
                    "Default to conversational prose with light bullets when useful. Avoid turning "
                    "ordinary chat into a report."
                )
            )
        )
        emoji_note = (
            "A small amount of matching emoji/slang is okay because the user uses it."
            if style.emoji_ok
            else "Do not add decorative emoji unless it materially helps readability."
        )
        return (
            f"Conversation register: {style.register}. {address} {format_note} {emoji_note} "
            "Lightly mirror the user's cadence and wording, not their beliefs or aggression. "
            "Never caricature the user, overuse slang, imitate insults/slurs, or fake personal closeness. "
            "Do not start with canned greetings like 'Hello'/'Chào bạn' unless the user greeted first. "
            "Do not narrate your role, capabilities, or missing project records unless that limitation "
            "actually blocks the answer. For analysis, lead with the useful conclusion first; use "
            "formal report-style sectioning only when the user asks for it or complexity truly requires it."
        )

    @staticmethod
    def _posture_instruction(posture: ResponsePosture) -> str:
        instructions = {
            ResponsePosture.NORMAL: (
                "Be warm, technically useful, and lightly playful only when it fits."
            ),
            ResponsePosture.BRAINSTORM: (
                "Be curious and constructive, challenge assumptions, compare tradeoffs, "
                "and allow mild situational humor."
            ),
            ResponsePosture.DEBUGGING: (
                "Be calm and surgical: identify the likely failure boundary, give the "
                "smallest useful next check, and keep humor subtle."
            ),
            ResponsePosture.INCIDENT: (
                "Be serious and action-first. Use no humor. Separate confirmed facts from "
                "uncertainty and avoid blame."
            ),
            ResponsePosture.PLANNING: (
                "Protect a sustainable pace. Separate must-do work from deferrable work, "
                "surface dependencies, and do not manufacture urgency."
            ),
            ResponsePosture.CELEBRATION: (
                "Acknowledge concrete progress and allow a little more playful energy, "
                "without exaggerating what was achieved."
            ),
        }
        return instructions[posture]
