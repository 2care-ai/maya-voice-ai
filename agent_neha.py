import asyncio
import importlib.util
import logging
import os
from datetime import datetime
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

from livekit import agents, rtc
from livekit.agents import AgentServer, AgentSession, Agent, room_io
from livekit.agents.llm import ChatContext, ChatMessage
from livekit.agents.voice.events import ConversationItemAddedEvent
from livekit.agents.metrics import EOUMetrics, LLMMetrics, STTMetrics, TTSMetrics
from livekit.plugins import cartesia, deepgram, groq, noise_cancellation, silero

_room_agents: dict[str, "NehaAgent"] = {}

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

logger = logging.getLogger("agent")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(h)


def _load_neha_instructions() -> str:
    spec = importlib.util.spec_from_file_location("neha_prompt", _PROJECT_DIR / "neha-prompt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "NEHA_INSTRUCTIONS", "You are Neha. Be brief and friendly.")


NEHA_INSTRUCTIONS = _load_neha_instructions()


class NehaAgent(Agent):
    def __init__(self, chat_ctx: ChatContext | None = None) -> None:
        super().__init__(chat_ctx=chat_ctx or ChatContext(), instructions=NEHA_INSTRUCTIONS)
        self._silence_task = None
        self._last_speech_time = asyncio.get_event_loop().time()
        self._waiting_for_response = False

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
            self._waiting_for_response = True

        @self.session.on("conversation_item_added")
        def on_conversation_item(ev: ConversationItemAddedEvent) -> None:
            if isinstance(ev.item, ChatMessage) and ev.item.role == "assistant":
                self._waiting_for_response = False
                self._last_speech_time = asyncio.get_event_loop().time()
                text = (ev.item.text_content or "").strip()
                if text:
                    logger.info("[Neha speaks] %s", text)

        self._silence_task = asyncio.create_task(self._monitor_silence())
        await self.session.generate_reply(
            instructions="Say a brief greeting and ask how you can help.",
            allow_interruptions=True,
        )

    async def _monitor_silence(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.session.current_speech is not None:
                self._last_speech_time = asyncio.get_event_loop().time()
                continue
            if self._waiting_for_response:
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
        logger.info("[STT] time=%.3fs audio_duration=%.3fs streamed=%s @%s", metrics.duration, metrics.audio_duration, metrics.streamed, ts)

    async def _log_eou(self, metrics: EOUMetrics) -> None:
        ts = datetime.fromtimestamp(metrics.timestamp).strftime("%H:%M:%S")
        logger.info("[STT EOU] eou_delay=%.3fs transcription_delay=%.3fs @%s", metrics.end_of_utterance_delay, metrics.transcription_delay, ts)

    async def _log_llm(self, metrics: LLMMetrics) -> None:
        ts = datetime.fromtimestamp(metrics.timestamp).strftime("%H:%M:%S")
        logger.info("[LLM] prompt_tokens=%d completion_tokens=%d (total=%d) ttft=%.3fs duration=%.3fs tps=%.1f cancelled=%s @%s", metrics.prompt_tokens, metrics.completion_tokens, metrics.total_tokens, metrics.ttft, metrics.duration, metrics.tokens_per_second, metrics.cancelled, ts)

    async def _log_tts(self, metrics: TTSMetrics) -> None:
        ts = datetime.fromtimestamp(metrics.timestamp).strftime("%H:%M:%S")
        logger.info("[TTS] ttfb=%.3fs duration=%.3fs audio_duration=%.3fs chars=%d cancelled=%s @%s", metrics.ttfb, metrics.duration, metrics.audio_duration, metrics.characters_count, metrics.cancelled, ts)

    def get_transcript(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for msg in self._chat_ctx.messages():
            if msg.role not in ("user", "assistant"):
                continue
            text = (msg.text_content or "").strip()
            if text:
                out.append({"role": msg.role, "content": text})
        return out


async def on_session_end(ctx: agents.JobContext) -> None:
    logger.info("Session ended: room=%s", ctx.room.name)
    agent = _room_agents.pop(ctx.room.name, None)
    webhook_url = os.getenv("SESSION_END_WEBHOOK_URL")
    if not webhook_url or not agent:
        return
    payload = {"roomName": ctx.room.name, "transcript": agent.get_transcript()}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status >= 400:
                    logger.warning("Transcript webhook failed: %s %s body=%s", resp.status, resp.reason, await resp.text())
                else:
                    logger.info("Transcript sent to webhook for room=%s", ctx.room.name)
    except Exception as e:
        logger.warning("Transcript webhook error for room=%s: %s", ctx.room.name, e)


def _prewarm(proc: agents.JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(min_speech_duration=0.05, min_silence_duration=0.25, activation_threshold=0.3)


server = AgentServer()
server.setup_fnc = _prewarm


@server.rtc_session(agent_name="neha-agent", on_session_end=on_session_end)
async def neha_agent(ctx: agents.JobContext):
    logger.info("Neha agent started: room=%s", ctx.room.name)
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi"),
        llm=groq.LLM(
            model="meta-llama/llama-4-maverick-17b-128e-instruct",
            api_key=os.getenv("GROQ_API_KEY"),
            max_completion_tokens=120,
        ),
        tts=cartesia.TTS(
            model="sonic-3",
            voice=os.getenv("CARTESIA_VOICE_ID"),
            api_key=os.getenv("CARTESIA_API_KEY"),
            speed=float(os.getenv("CARTESIA_SPEED", "1.0")),
        ),
        vad=ctx.proc.userdata["vad"],
        turn_detection="vad",
        preemptive_generation=True,
        min_interruption_duration=0.5,
        min_interruption_words=2,
        min_endpointing_delay=1.0,
        max_endpointing_delay=3.0,
    )
    agent = NehaAgent(chat_ctx=ChatContext())
    _room_agents[ctx.room.name] = agent
    await session.start(
        room=ctx.room,
        agent=agent,
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
