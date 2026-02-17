import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import AsyncIterable
from dotenv import load_dotenv
import aiohttp

from livekit import agents, rtc
from livekit.agents import AgentServer, AgentSession, Agent, room_io
from livekit.agents.llm import ChatContext, ChatMessage
from livekit.agents.metrics import EOUMetrics, LLMMetrics, STTMetrics, TTSMetrics
from livekit.agents.voice import ModelSettings
from livekit.plugins import deepgram, elevenlabs, groq, noise_cancellation, silero

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

logger = logging.getLogger("agent")
logger.setLevel(logging.INFO)

TRANSCRIPT_DIR = _PROJECT_DIR / "transcripts"


def _append_transcript(room_name: str, role: str, content: str) -> None:
    """Append one turn to local transcript file."""
    if not content or not room_name:
        return
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"{room_name}.json"
    try:
        existing = []
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
        existing.append({"role": role, "content": content})
        path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("Transcript append failed: %s", e)


class Assistant(Agent):
    def __init__(
        self,
        chat_ctx: ChatContext | None = None,
        room_name: str | None = None,
        instructions: str | None = None,
    ) -> None:
        super().__init__(chat_ctx=chat_ctx, instructions=instructions or "")
        self._silence_task = None
        self._last_speech_time = asyncio.get_event_loop().time()
        self._room_name = room_name or ""

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

        await self.session.generate_reply(instructions="Begin the conversation with your first flow step.")

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
                _append_transcript(self._room_name, "agent", agent_message)

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        user_text = getattr(new_message, "text_content", None) or ""
        if user_text:
            logger.info("User: %s", user_text.strip())
            _append_transcript(self._room_name, "user", user_text.strip())
        # Single-prompt flow: no state or step injection; LLM follows instructions.

async def _send_transcript_webhook(ctx: agents.JobContext) -> None:
    """Read transcript from local file and POST to SESSION_END_WEBHOOK_URL."""
    webhook_url = (os.getenv("SESSION_END_WEBHOOK_URL") or "").strip()
    if not webhook_url:
        return
    transcript: list = []
    path = TRANSCRIPT_DIR / f"{ctx.room.name}.json"
    if path.exists():
        try:
            transcript = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Transcript read failed: %s", e)
        try:
            path.unlink()
        except Exception:
            pass
    metadata_str = ctx.job.metadata or ctx.room.metadata or "{}"
    try:
        dial_info = json.loads(metadata_str)
    except json.JSONDecodeError:
        dial_info = {}
    payload = {
        "room_name": ctx.room.name,
        "phone_number": dial_info.get("phone_number"),
        "transcript": transcript,
        "ended_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status >= 400:
                    logger.warning(
                        "Session end webhook failed: status=%s url=%s",
                        resp.status,
                        webhook_url,
                    )
                else:
                    logger.info("Session transcript sent to webhook: room=%s", ctx.room.name)
    except Exception as e:
        logger.warning("Session end webhook error: %s", e)


async def on_session_end(ctx: agents.JobContext) -> None:
    logger.info("Session ended: room=%s", ctx.room.name)
    await _send_transcript_webhook(ctx)

def _prewarm(proc: agents.JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=0.05,
        min_silence_duration=0.25,
        activation_threshold=0.3,
    )


server = AgentServer()
server.setup_fnc = _prewarm


@server.rtc_session(agent_name=os.getenv("LIVEKIT_AGENT_NAME", "maya-agent"), on_session_end=on_session_end)
async def my_agent(ctx: agents.JobContext):
    # Job metadata is set by explicit agent dispatch (create_dispatch with metadata). Fall back to room metadata.
    metadata_str = ctx.job.metadata or ctx.room.metadata or "{}"
    try:
        dial_info = json.loads(metadata_str)
    except json.JSONDecodeError:
        dial_info = {}
    phone_number = dial_info.get("phone_number")
    instructions = dial_info.get("prompt") or ""
    logger.info("Agent started: room=%s phone=%s prompt_len=%s", ctx.room.name, phone_number or "N/A", len(instructions))

    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi"),  # multi = Hindi + English (Hinglish) code-switching
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
        agent=Assistant(chat_ctx=ChatContext(), room_name=ctx.room.name, instructions=instructions),
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