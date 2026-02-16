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
class ConfirmationResult:
    aware: bool
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


@dataclass
class TimelineResult:
    timeline: str
    summary: str


@dataclass
class GeographyResult:
    where_from: str
    willing_to_travel_answer: str


@dataclass
class ClosingResult:
    done: bool = True


# Shared rules included in every task because the framework *replaces* the system prompt when a task runs (chat context holds messages only). So each task must carry the full rules.
_COMMON = (
    "Speak ONLY Hindi (Hinglish) and English. Default is Hinglish; use English when the user speaks English or asks for it. "
    "One question per turn. If they ask something else, answer briefly then return to the step. "
    "Do NOT repeat or summarize what the user said. Acknowledge briefly and move on or call step_done. "
    "When they are unsure or defer: say one short line (acknowledge and ask next step) then call step_done so the user always hears a reply. "
    "When they refuse to share: respond briefly then call step_done. "
    "After calling step_done, the next step will begin. When they share something difficult (cancer, stage, treatment), one short empathetic line then the step. "
    "Output format (STRICT): Your reply is sent directly to TTS. Output ONLY the exact words to speak. No labels, instructions, parentheticals, or reasoning. One spoken line only. Soft, human tone."
)

TaskResult_T = TypeVar("TaskResult_T")


class MayaTask(AgentTask[TaskResult_T]):
    pass


class OpeningTask(MayaTask[OpeningResult]):
    def __init__(self, patient_name: str = "") -> None:
        super().__init__(
            instructions=f"""You are Maya, from the Everhope Oncology. {_COMMON}
            You already asked if it is a good time to talk. When the user says yes (good time) or no (not good time), you MUST call step_done(good_time=true) or step_done(good_time=false) in that same turn—say one short line (e.g. Let's continue then / No problem) and call step_done so we move to the next step. Do not wait for another message."""
        )
        self._patient_name = (patient_name or "").strip()

    async def on_enter(self) -> None:
        if self._patient_name:
            script = f"Hello, I am Maya from Everhope Oncology calling regarding your recent medical test. May I speak with {self._patient_name}?"
        else:
            script = "Hello, I am Maya from Everhope Oncology calling regarding your recent medical test. Is this a good time to talk?"
        await self.session.say(script, allow_interruptions=True)

    @function_tool(description="Call when the user has said if it is a good time or not. Pass good_time true or false.")
    async def step_done(self, good_time: bool = True) -> None:
        self.complete(OpeningResult(good_time=good_time))


class ScheduleCallbackTask(MayaTask[ScheduleCallbackResult]):
    def __init__(self) -> None:
        super().__init__(
            instructions=f"""You are Maya, from the Everhope Oncology. {_COMMON}
            The user said it is not a good time. Ask when would be a good time to call back. When they share, call step_done."""
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Output only the words to speak (no labels): politely ask when would be a good time to call them back. One question. When they answer, call step_done."
        )

    @function_tool(description="Call when the user has said when to call back.")
    async def step_done(self, unused: str = "") -> None:
        self.complete(ScheduleCallbackResult(callback_preference="", summary=""))


class ConfirmationTask(MayaTask[ConfirmationResult]):
    def __init__(self) -> None:
        super().__init__(
            instructions=f"""You are Maya, from the Everhope Oncology. {_COMMON}
            Ask whether they have discussed the report with their doctor.
            CRITICAL: If the user says they have NOT discussed with their doctor, never say "cancer", "positive", or "diagnosis". Give a brief Everhope intro (we help with cancer care coordination, consultation and treatment) then call step_done.
            If they HAVE discussed with the doctor, acknowledge briefly and call step_done. When they answer, call step_done."""
        )

    _CONFIRMATION_OPENING = (
        "We understand you've had your tests recently. I'll need a few details to better understand your situation—have you discussed the report with your doctor yet?"
    )

    async def on_enter(self) -> None:
        await self.session.say(self._CONFIRMATION_OPENING, allow_interruptions=True)

    @function_tool(description="Call when they have answered whether they discussed the report with their doctor.")
    async def step_done(self, unused: str = "") -> None:
        self.complete(ConfirmationResult(aware=True, summary=""))


class DiagnosisQualificationTask(MayaTask[DiagnosisResult]):
    def __init__(self) -> None:
        super().__init__(
            instructions=f"""You are Maya. {_COMMON}
            Diagnosis in 2 questions only. Q1: type of cancer and stage. Q2: biopsy done? and whether it has spread.
            If they are unsure or defer: say one short line (acknowledge and ask if they've started treatment yet) then call step_done. When you have enough to move on, call step_done."""
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Output only the words to speak (no labels): Ask type and stage, then biopsy and spread. If they are unsure or defer: say one short line (acknowledge and ask if they've started treatment yet) then call step_done."
        )

    @function_tool(description="Call when done with diagnosis step (after Q1 and Q2 or when they are unsure).")
    async def step_done(self, unused: str = "") -> None:
        self.complete(DiagnosisResult(cancer_type="", biopsy_done=False, stage_known=False, metastasis_known=False, summary=""))


class TreatmentStatusTask(MayaTask[TreatmentResult]):
    def __init__(self) -> None:
        super().__init__(
            instructions=f"""You are Maya. {_COMMON}
            The last message already asked if they've started treatment. Do NOT ask again. When they answer: if NOT started call step_done(started=false) then ask when they plan to start or look at treatment; if they HAVE started acknowledge briefly and call step_done(started=true). Never repeat the treatment question."""
        )

    async def on_enter(self) -> None:
        pass

    @function_tool(description="Call when they have answered about treatment. Pass started=true if they have started, false if not.")
    async def step_done(self, started: bool = False) -> None:
        self.complete(TreatmentResult(started=started, hospital=None, surgery_planned=None, chemo_advised=None, summary=""))


class DecisionTimelineTask(MayaTask[TimelineResult]):
    def __init__(self) -> None:
        super().__init__(
            instructions=f"""You are Maya. {_COMMON}
            The last message already asked when they plan to start or look at treatment. Do NOT ask again. When they answer, call step_done."""
        )

    async def on_enter(self) -> None:
        pass

    @function_tool(description="Call when they have said when they plan to start or look at treatment.")
    async def step_done(self, unused: str = "") -> None:
        self.complete(TimelineResult(timeline="", summary=""))


class GeographyTask(MayaTask[GeographyResult]):
    def __init__(self) -> None:
        super().__init__(
            instructions=f"""You are Maya. {_COMMON}
            Build rapport. Ask where they're from, then ask if they'd be willing to travel for treatment if needed. When done, call step_done. Friendly, human talk—not a checklist."""
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Output only the words to speak (no labels): Ask in a friendly way—May I know where you're from? Then ask if they'd be willing to travel for treatment if needed. When done, call step_done."
        )

    @function_tool(description="Call when you have asked where they're from and about travel willingness.")
    async def step_done(self, unused: str = "") -> None:
        self.complete(GeographyResult(where_from="", willing_to_travel_answer=""))


class ClosingTask(MayaTask[ClosingResult]):
    def __init__(self, is_callback_path: bool = False) -> None:
        super().__init__(
            instructions=f"""You are Maya. {_COMMON}
            This is the closing step. Thank them. If callback path: say we will call back at the preferred time, take care. If main path: say you look forward to helping, next steps, wish them well. When done, call step_done."""
        )
        self._is_callback_path = is_callback_path

    async def on_enter(self) -> None:
        if self._is_callback_path:
            await self.session.generate_reply(
                instructions="Output only the words to speak (no labels): thank them, say we will call back at their preferred time, wish them well. Then call step_done."
            )
        else:
            await self.session.generate_reply(
                instructions="Output only the words to speak (no labels): thank them, say you look forward to helping and wish them a hopeful day. Then call step_done."
            )

    @function_tool(description="Call when you have finished the closing and the call can end.")
    async def step_done(self, unused: str = "") -> None:
        self.complete(ClosingResult())
