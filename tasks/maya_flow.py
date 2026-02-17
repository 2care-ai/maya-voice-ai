import logging
from dataclasses import dataclass
from typing import TypeVar

from livekit.agents import AgentTask, function_tool

logger = logging.getLogger("agent")


@dataclass
class OpeningResult:
    good_time: bool


@dataclass
class ScheduleCallbackResult:
    callback_preference: str
    summary: str


@dataclass
class DiagnosisResult:
    cancer_type: str
    biopsy_done: bool
    stage_known: bool
    metastasis_known: bool
    summary: str


@dataclass
class TreatmentResult:
    started: bool
    hospital: str | None
    surgery_planned: bool | None
    chemo_advised: bool | None
    summary: str
    timeline: str = ""


@dataclass
class GeographyResult:
    where_from: str
    willing_to_travel_answer: str


@dataclass
class ClosingResult:
    done: bool = True


# Shared rules included in every task because the framework *replaces* the system prompt when a task runs (chat context holds messages only). So each task must carry the full rules.
# For generate_reply(instructions=...): framework merges with agent.instructions; reinforce output rule so only spoken words are produced.
_OUTPUT_ONLY = "Output only the words to speak (no labels, no preamble, no summaries). Do not type step_done or any JSON—only the spoken line: "
_COMMON = (
    "Speak ONLY Hindi (Hinglish) and English. Default is Hinglish; use English when the user speaks English or asks for it. "
    "One question per turn. If they ask something else, answer briefly then return to the step. "
    "FLOW: Before calling step_done you MUST say one short acknowledgement so the user hears this step is complete (e.g. Got it, Thanks, Sahi hai, I understand—then call step_done). Never call step_done without saying something first in the same turn. "
    "When they are unsure or defer: say one short line (acknowledge and transition) then call step_done so the user always hears a reply. "
    "When they refuse to share: acknowledge briefly then call step_done. "
    "After step_done the next stage will start; the user should always hear a clear close to the current topic before the next one. When they share something difficult (cancer, stage, treatment), one short empathetic line then the step. "
    "Output format (STRICT): Your reply is sent directly to TTS—the user hears every character. Output ONLY the exact words to speak. Never output: tool names (e.g. good_time_yes, good_time_no, step_done), function names, JSON, code, labels, 'Now:', 'Ask about...', step directives, instructions, parentheticals, reasoning, or preamble. Never output conversation summaries or state (e.g. 'The user confirmed...', 'The user has received...', 'To begin diagnosis:', numbered lists like '1. What type... 2. Whether...'). Never announce language choice. When your only action is to call a tool, output NOTHING—leave your reply empty; the tool will speak. When you call a tool AND say something in the same turn: your reply must contain ONLY the spoken words (e.g. Got it, Thanks). Never write the tool name, step_done, or any JSON or curly braces in your reply—the system calls the tool separately. If you would not say it on a phone call, do not output it. One spoken line only. Soft, human tone."
)

TaskResult_T = TypeVar("TaskResult_T")


class MayaTask(AgentTask[TaskResult_T]):
    pass


class OpeningTask(MayaTask[OpeningResult]):
    def __init__(self, patient_name: str = "") -> None:
        super().__init__(
            instructions=f"""You are Maya, from the Everhope Oncology. {_COMMON}
            When the user indicates it is a good time to talk, call good_time_yes only—do not type any words in your reply (no Sahi hai, no tool name). When they indicate it is not a good time, call good_time_no only—do not type any words. Your reply must be empty when you call these tools; the tool will speak the acknowledgement."""
        )
        self._patient_name = (patient_name or "").strip()

    async def on_enter(self) -> None:
        if self._patient_name:
            script = f"Hello, I am Maya from Everhope Oncology calling regarding your recent medical test. May I speak with {self._patient_name}?"
        else:
            script = "Hello, I am Maya from Everhope Oncology calling regarding your recent medical test. Is this a good time to talk?"
        await self.session.say(script, allow_interruptions=True)

    @function_tool(description="Call when the user has said yes, it is a good time to talk. Do not output any text in your reply when calling this—reply with no words.")
    async def good_time_yes(self, unused: str = "") -> None:
        await self.session.say("Sahi hai, thank you. Let me ask you a few quick questions.", allow_interruptions=True)
        self.complete(OpeningResult(good_time=True))

    @function_tool(description="Call when the user has said no, it is not a good time to talk. Do not output any text in your reply when calling this—reply with no words.")
    async def good_time_no(self, unused: str = "") -> None:
        await self.session.say("No problem, we'll call you back. Take care.", allow_interruptions=True)
        self.complete(OpeningResult(good_time=False))


class ScheduleCallbackTask(MayaTask[ScheduleCallbackResult]):
    def __init__(self) -> None:
        super().__init__(
            instructions=f"""You are Maya, from the Everhope Oncology. {_COMMON}
            After your opening (asking when to call back), when the user responds with a time: acknowledge in one line (e.g. Got it, we'll call you then / Sahi hai, we'll reach out then), then call step_done."""
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions=f"{_OUTPUT_ONLY}Briefly acknowledge (e.g. No problem), then ask when would be a good time to call them back. One question. When they answer, acknowledge (e.g. Got it, we'll call you then) and call step_done."
        )

    @function_tool(description="Call when the user has said when to call back.")
    async def step_done(self, unused: str = "") -> None:
        self.complete(ScheduleCallbackResult(callback_preference="", summary=""))


class DiagnosisQualificationTask(MayaTask[DiagnosisResult]):
    def __init__(self) -> None:
        super().__init__(
            instructions=f"""You are Maya. {_COMMON}
            The conversation may have just asked whether they discussed the report with their doctor. CRITICAL: If they have NOT discussed with their doctor, never say "cancer", "positive", or "diagnosis"—only that their doctor can guide them and Everhope helps with cancer care coordination and treatment.
            Diagnosis in 2 questions only. Q1: type of cancer and stage. Q2: biopsy done? and whether it has spread. When you have enough to move on or they defer: call step_done. If they defer: say one short line (acknowledge and ask if they've started treatment yet) then call step_done."""
        )

    async def on_enter(self) -> None:
        await self.session.say(
            "Kaun sa cancer detect hua hai aur stage kya hai?",
            allow_interruptions=True,
        )

    @function_tool(description="Call when done with diagnosis step (after Q1 and Q2 or when they are unsure).")
    async def step_done(self, unused: str = "") -> None:
        await self.session.generate_reply(
            instructions=f"{_OUTPUT_ONLY}Acknowledge with empathy (e.g. I understand how hard this is, You are not alone). One line only."
        )
        self.complete(DiagnosisResult(cancer_type="", biopsy_done=False, stage_known=False, metastasis_known=False, summary=""))


class TreatmentStatusTask(MayaTask[TreatmentResult]):
    def __init__(self) -> None:
        super().__init__(
            instructions=f"""You are Maya. {_COMMON}
            Your opening asked whether they have started treatment. When they respond: if they HAVE started, acknowledge and call step_done(started=true). If they have NOT started, do NOT call step_done yet—acknowledge and ask when they plan to start or look at treatment (one short question). When they later answer that timeline question, acknowledge and call step_done(started=false, timeline=short summary of when they plan)."""
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions=f"{_OUTPUT_ONLY}Ask whether they have started treatment yet. One short question (e.g. Have you started your treatment yet? / Kya aapne treatment shuru kar diya?). Match the conversation language (Hinglish/English)."
        )

    @function_tool(description="Call when they have answered about treatment. Pass started=true if they have started. Pass started=false and timeline=<when they plan> only after they have answered the 'when do you plan to start' question. When calling this tool, your text reply must be ONLY the spoken acknowledgement—never include the tool name or JSON.")
    async def step_done(self, started: bool = False, timeline: str = "") -> None:
        await self.session.generate_reply(
            instructions=f"{_OUTPUT_ONLY}Acknowledge with warmth in one short line (e.g. Got it, Thanks / That helps). Nothing else—no tool name, no JSON."
        )
        self.complete(TreatmentResult(started=started, hospital=None, surgery_planned=None, chemo_advised=None, summary="", timeline=timeline))


class GeographyTask(MayaTask[GeographyResult]):
    def __init__(self) -> None:
        super().__init__(
            instructions=f"""You are Maya. {_COMMON}
            After your opening (where from / travel?), when the user responds: acknowledge in one line (e.g. Got it / Thanks / Sahi hai), then ask the next question or call step_done when you have both answers. Friendly, human—not a checklist. Always acknowledge before step_done."""
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions=f"{_OUTPUT_ONLY}One short transition then the first question only (e.g. Just a couple more things. Where are you from?). Do NOT say what the previous discussion was about. Only the transition and the question."
        )

    @function_tool(description="Call when you have asked where they're from and about travel willingness.")
    async def step_done(self, unused: str = "") -> None:
        self.complete(GeographyResult(where_from="", willing_to_travel_answer=""))


class ClosingTask(MayaTask[ClosingResult]):
    def __init__(self, is_callback_path: bool = False) -> None:
        super().__init__(
            instructions=f"""You are Maya. {_COMMON}
            This is the closing step. After your closing speech: when the user responds (or when done), acknowledge if needed then call step_done. Say a clear closing first (thank them; callback path: we'll call back at your preferred time, take care; main path: look forward to helping, wish them well). User must hear a clear goodbye before step_done."""
        )
        self._is_callback_path = is_callback_path

    async def on_enter(self) -> None:
        if self._is_callback_path:
            await self.session.generate_reply(
                instructions=f"{_OUTPUT_ONLY}Thank them, say we will call back at their preferred time, wish them well. Then call step_done."
            )
        else:
            await self.session.generate_reply(
                instructions=f"{_OUTPUT_ONLY}Thank them, say you look forward to helping and wish them a hopeful day. Then call step_done."
            )

    @function_tool(description="Call when you have finished the closing and the call can end.")
    async def step_done(self, unused: str = "") -> None:
        self.complete(ClosingResult())
