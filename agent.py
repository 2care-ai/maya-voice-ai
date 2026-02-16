import asyncio
import importlib.util
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import AsyncIterable

import aiohttp
from dotenv import load_dotenv

from livekit import agents, rtc
from livekit.agents import AgentServer, AgentSession, Agent, room_io, function_tool, RunContext
from livekit.agents.llm import ChatContext, ChatMessage
from livekit.agents.voice.events import ConversationItemAddedEvent
from livekit.agents.metrics import EOUMetrics, LLMMetrics, STTMetrics, TTSMetrics
from livekit.agents.voice import ModelSettings
from livekit.plugins import deepgram, elevenlabs, groq, noise_cancellation, silero

from livekit.agents.beta.workflows import TaskGroup

from everhope_store import get_everhope_knowledge_base
from tasks.maya_flow import (
    ClosingTask,
    ConfirmationTask,
    DecisionTimelineTask,
    DiagnosisQualificationTask,
    GeographyTask,
    OpeningTask,
    ScheduleCallbackTask,
    TreatmentStatusTask,
)

_room_agents: dict[str, "Assistant"] = {}

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

logger = logging.getLogger("agent")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(h)

def _load_maya_instructions(compact: bool = True) -> str:
    spec = importlib.util.spec_from_file_location(
        "maya_prompt", _PROJECT_DIR / "maya-prompt.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.get_maya_instructions_compact() if compact else mod.get_maya_instructions()

AGENT_INSTRUCTIONS = _load_maya_instructions(compact=True)

class _FlowResults:
    __slots__ = ("task_results",)

    def __init__(self, task_results: dict) -> None:
        self.task_results = task_results


class Assistant(Agent):
    def __init__(
        self,
        chat_ctx: ChatContext | None = None,
        patient_name: str = "",
    ) -> None:
        super().__init__(chat_ctx=chat_ctx, instructions=AGENT_INSTRUCTIONS)
        self._silence_task = None
        self._last_speech_time = asyncio.get_event_loop().time()
        self._flow_results = None
        self._patient_name = patient_name or ""

    @function_tool(
        description="Fetch Everhope center locations and details. Use when the user asks about centers, locations, or addresses. The return value is for you to summarize in your own words—do not read it aloud verbatim; give a short spoken answer (e.g. city names and one line per center).",
        raw_schema={
            "type": "function",
            "name": "get_center_info",
            "description": "Fetch Everhope center locations and details. Use when the user asks about centers, locations, or addresses. Summarize the result in your own words when replying; do not read the raw output aloud.",
            "parameters": {
                "type": "object",
                "properties": {
                    "_": {"type": "string", "description": "Unused; omit or leave empty."},
                },
            },
        },
    )
    async def get_center_info(
        self, raw_arguments: dict, ctx: RunContext
    ) -> str:
        try:
            content = await asyncio.to_thread(get_everhope_knowledge_base)
            if not content:
                return "Center information is not available right now."
            return content
        except Exception as e:
            logger.warning("get_center_info failed: %s", e)
            return "I couldn't fetch center details at the moment."

    async def on_enter(self) -> None:
        def on_stt(metrics: STTMetrics) -> None:
            asyncio.create_task(self._log_stt(metrics))
        def on_eou(metrics: EOUMetrics) -> None:
            asyncio.create_task(self._log_eou(metrics))
        def on_llm(metrics: LLMMetrics) -> None:
            asyncio.create_task(self._log_llm(metrics))
        def on_tts(metrics: TTSMetrics) -> None:
            asyncio.create_task(self._log_tts(metrics))

        self.session.stt.on("metrics_collected", on_stt)
        self.session.stt.on("eou_metrics_collected", on_eou)
        self.session.llm.on("metrics_collected", on_llm)
        self.session.tts.on("metrics_collected", on_tts)

        @self.session.on("user_speech_committed")
        def on_user_speech(_msg):
            self._last_speech_time = asyncio.get_event_loop().time()

        @self.session.on("conversation_item_added")
        def on_conversation_item(ev: ConversationItemAddedEvent) -> None:
            if isinstance(ev.item, ChatMessage) and ev.item.role == "assistant":
                text = (ev.item.text_content or "").strip()
                if text:
                    logger.info("[Agent speaks] %s", text)

        self._silence_task = asyncio.create_task(self._monitor_silence())

        opening_group = TaskGroup(chat_ctx=self.chat_ctx)
        opening_group.add(
            lambda: OpeningTask(patient_name=self._patient_name),
            id="opening",
            description="Opening and good time to speak",
        )
        results_opening = await opening_group
        opening_result = results_opening.task_results["opening"]
        good_time = opening_result.good_time

        all_task_results: dict = {"opening": opening_result}

        if good_time:
            collect_group = TaskGroup(chat_ctx=self.chat_ctx)
            collect_group.add(lambda: ConfirmationTask(), id="confirmation", description="Discuss report with doctor")
            collect_group.add(lambda: DiagnosisQualificationTask(), id="diagnosis", description="Diagnosis stage")
            collect_group.add(lambda: TreatmentStatusTask(), id="treatment", description="Treatment status")
            results_collect = await collect_group
            all_task_results.update(results_collect.task_results)

            treatment_result = results_collect.task_results["treatment"]
            treatment_started = getattr(treatment_result, "started", False)

            if treatment_started:
                rest_group = TaskGroup(chat_ctx=self.chat_ctx)
                rest_group.add(lambda: GeographyTask(), id="geography", description="Geography and travel")
                rest_group.add(lambda: ClosingTask(is_callback_path=False), id="closing", description="Thank and close")
                results_rest = await rest_group
                all_task_results.update(results_rest.task_results)
            else:
                rest_group = TaskGroup(chat_ctx=self.chat_ctx)
                rest_group.add(lambda: DecisionTimelineTask(), id="timeline", description="Decision timeline")
                rest_group.add(lambda: GeographyTask(), id="geography", description="Geography and travel")
                rest_group.add(lambda: ClosingTask(is_callback_path=False), id="closing", description="Thank and close")
                results_rest = await rest_group
                all_task_results.update(results_rest.task_results)
        else:
            callback_group = TaskGroup(chat_ctx=self.chat_ctx)
            callback_group.add(lambda: ScheduleCallbackTask(), id="schedule_callback", description="Schedule callback")
            callback_group.add(lambda: ClosingTask(is_callback_path=True), id="closing", description="Close after callback")
            results_callback = await callback_group
            all_task_results.update(results_callback.task_results)

        self._flow_results = _FlowResults(task_results=all_task_results)

        logger.info("Flow complete: task_results=%s", list(all_task_results.keys()))
        await self.session.generate_reply(
            instructions="Flow complete. Respond warmly and briefly to anything they say."
        )

    async def _monitor_silence(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.session.current_speech is not None:
                self._last_speech_time = asyncio.get_event_loop().time()
                continue
            elapsed = asyncio.get_event_loop().time() - self._last_speech_time
            if elapsed >= 10.0:
                logger.info("Silence re-engagement (10s)")
                self._last_speech_time = asyncio.get_event_loop().time()
                await self.session.say("Are you still there?", allow_interruptions=True)
                await asyncio.sleep(2.0)
                self._last_speech_time = asyncio.get_event_loop().time()

    async def _log_stt(self, metrics: STTMetrics) -> None:
        ts = datetime.fromtimestamp(metrics.timestamp).strftime("%H:%M:%S")
        logger.info(
            "[STT] time=%.3fs audio_duration=%.3fs streamed=%s @%s",
            metrics.duration, metrics.audio_duration, metrics.streamed, ts,
        )

    async def _log_eou(self, metrics: EOUMetrics) -> None:
        ts = datetime.fromtimestamp(metrics.timestamp).strftime("%H:%M:%S")
        logger.info(
            "[STT EOU] eou_delay=%.3fs transcription_delay=%.3fs @%s",
            metrics.end_of_utterance_delay, metrics.transcription_delay, ts,
        )

    async def _log_llm(self, metrics: LLMMetrics) -> None:
        ts = datetime.fromtimestamp(metrics.timestamp).strftime("%H:%M:%S")
        logger.info(
            "[LLM] prompt_tokens=%d completion_tokens=%d (total=%d) ttft=%.3fs duration=%.3fs tps=%.1f cancelled=%s @%s",
            metrics.prompt_tokens, metrics.completion_tokens, metrics.total_tokens,
            metrics.ttft, metrics.duration, metrics.tokens_per_second, metrics.cancelled, ts,
        )

    async def _log_tts(self, metrics: TTSMetrics) -> None:
        ts = datetime.fromtimestamp(metrics.timestamp).strftime("%H:%M:%S")
        logger.info(
            "[TTS] ttfb=%.3fs duration=%.3fs audio_duration=%.3fs chars=%d cancelled=%s @%s",
            metrics.ttfb, metrics.duration, metrics.audio_duration,
            metrics.characters_count, metrics.cancelled, ts,
        )

    def get_transcript(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for msg in self._chat_ctx.messages():
            if msg.role not in ("user", "assistant"):
                continue
            text = (msg.text_content or "").strip()
            if text:
                out.append({"role": msg.role, "content": text})
        return out

    def get_flow_results_payload(self) -> dict | None:
        if not self._flow_results:
            return None
        out = {}
        for task_id, result in self._flow_results.task_results.items():
            if hasattr(result, "__dict__"):
                out[task_id] = {k: v for k, v in result.__dict__.items() if not k.startswith("_")}
            else:
                out[task_id] = str(result)
        return out


async def on_session_end(ctx: agents.JobContext) -> None:
    logger.info("Session ended: room=%s", ctx.room.name)
    agent = _room_agents.pop(ctx.room.name, None)
    webhook_url = os.getenv("SESSION_END_WEBHOOK_URL")
    if not webhook_url or not agent:
        return
    transcript = agent.get_transcript()
    payload = {"roomName": ctx.room.name, "transcript": transcript}
    flow_results = agent.get_flow_results_payload()
    if flow_results:
        payload["flowResults"] = flow_results
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status >= 400:
                    logger.warning(
                        "Transcript webhook failed: %s %s body=%s",
                        resp.status,
                        resp.reason,
                        await resp.text(),
                    )
                else:
                    logger.info("Transcript sent to webhook for room=%s", ctx.room.name)
    except Exception as e:
        logger.warning("Transcript webhook error for room=%s: %s", ctx.room.name, e)


def _prewarm(proc: agents.JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=0.05,
        min_silence_duration=0.25,
        activation_threshold=0.3,
    )

server = AgentServer()
server.setup_fnc = _prewarm


@server.rtc_session(agent_name="maya-agent", on_session_end=on_session_end)
async def my_agent(ctx: agents.JobContext):
    metadata_str = ctx.job.metadata or ctx.room.metadata or "{}"
    try:
        dial_info = json.loads(metadata_str)
    except json.JSONDecodeError:
        dial_info = {}
    phone_number = dial_info.get("phone_number")
    patient_name = dial_info.get("patient_name") or ""
    logger.info("Agent started: room=%s phone=%s patient=%s", ctx.room.name, phone_number or "N/A", patient_name or "N/A")

    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi"),
        llm=groq.LLM(
            model="meta-llama/llama-4-maverick-17b-128e-instruct",
            api_key=os.getenv("GROQ_API_KEY"),
            max_completion_tokens=120,
        ),
        tts=elevenlabs.TTS(
            model="eleven_flash_v2_5",
            voice_id=os.getenv("ELEVEN_LABS_VOICE_ID") or os.getenv("ELEVEN_LABS_DEFAULT_VOICE_ID"),
            api_key=os.getenv("ELEVEN_LABS_API_KEY"),
        ),
        vad=ctx.proc.userdata["vad"],
        turn_detection="vad",
        preemptive_generation=True,
        min_interruption_duration=0.5,
        min_interruption_words=2,
        min_endpointing_delay=1.0,
        max_endpointing_delay=3.0,
    )

    assistant = Assistant(chat_ctx=ChatContext(), patient_name=patient_name)
    _room_agents[ctx.room.name] = assistant
    await session.start(
        room=ctx.room,
        agent=assistant,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
