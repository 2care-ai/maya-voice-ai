import asyncio
import importlib.util
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import AsyncIterable
from dotenv import load_dotenv

from livekit import agents, rtc
from livekit.agents import AgentServer, AgentSession, Agent, room_io, function_tool, RunContext
from livekit.agents.llm import ChatContext, ChatMessage
from livekit.agents.metrics import EOUMetrics, LLMMetrics, STTMetrics, TTSMetrics
from livekit.agents.voice import ModelSettings
from livekit.plugins import deepgram, elevenlabs, groq, noise_cancellation, silero

from flow import FlowState, get_step_instruction, get_next_state
from everhope_store import get_everhope_knowledge_base

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

logger = logging.getLogger("agent")
logger.setLevel(logging.INFO)

def _load_maya_instructions(compact: bool = True) -> str:
    spec = importlib.util.spec_from_file_location(
        "maya_prompt", _PROJECT_DIR / "maya-prompt.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.get_maya_instructions_compact() if compact else mod.get_maya_instructions()

AGENT_INSTRUCTIONS = _load_maya_instructions(compact=True)

class Assistant(Agent):
    def __init__(self, chat_ctx: ChatContext | None = None) -> None:
        super().__init__(chat_ctx=chat_ctx, instructions=AGENT_INSTRUCTIONS)
        self._silence_task = None
        self._last_speech_time = asyncio.get_event_loop().time()
        self._flow_state = FlowState.RECORDING_INTRO

    @function_tool(
        description="Fetch Everhope Oncology center locations and details. Use when the user asks about centers, locations, addresses",
        raw_schema={
            "type": "function",
            "name": "get_center_info",
            "description": "Fetch Everhope Oncology center locations and details. Use when the user asks about centers, locations, addresses",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
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

        self._silence_task = asyncio.create_task(self._monitor_silence())

        await self.session.generate_reply(
            instructions=get_step_instruction(self._flow_state)
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

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[rtc.AudioFrame]:
        chunks: list[str] = []
        async def tee() -> AsyncIterable[str]:
            async for chunk in text:
                chunks.append(chunk)
                yield chunk
        async for frame in Agent.default.tts_node(self, tee(), model_settings):
            yield frame
        if chunks:
            agent_message = "".join(chunks).strip()
            if agent_message:
                logger.info("Agent: %s", agent_message)

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        user_text = getattr(new_message, "text_content", None) or ""
        if user_text:
            logger.info("User: %s", user_text.strip())
        next_state = get_next_state(self._flow_state, str(user_text or ""))
        turn_ctx.add_message(
            role="user",
            content=f"[Current step—reply with only Maya's words, no prefix or label:] {get_step_instruction(next_state)}",
        )
        self._flow_state = next_state
        logger.info("Flow state -> %s", self._flow_state.value)

async def on_session_end(ctx: agents.JobContext) -> None:
    logger.info("Session ended: room=%s", ctx.room.name)


def _prewarm(proc: agents.JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=0.05,
        min_silence_duration=0.25,
        activation_threshold=0.3,
    )


server = AgentServer()
server.setup_fnc = _prewarm


@server.rtc_session(on_session_end=on_session_end)
async def my_agent(ctx: agents.JobContext):
    metadata_str = ctx.job.metadata or ctx.room.metadata or "{}"
    try:
        dial_info = json.loads(metadata_str)
    except json.JSONDecodeError:
        dial_info = {}
    phone_number = dial_info.get("phone_number")
    logger.info("Agent started: room=%s phone=%s", ctx.room.name, phone_number or "N/A")

    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="en"),
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
        min_interruption_words=0,
        min_endpointing_delay=0.5,
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(chat_ctx=ChatContext()),
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
