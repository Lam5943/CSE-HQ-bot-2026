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


@dataclass(frozen=True)
class PersonalityPolicy:
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

    def instruction_for(self, question: str) -> str:
        posture = self.classify(question)
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
            "</CSE_HQ_PERSONALITY>"
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
