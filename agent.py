import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from dotenv import load_dotenv

from livekit import agents, rtc
from livekit.agents import AgentServer, AgentSession, Agent, room_io
from livekit.agents import function_tool, RunContext
from livekit.agents.llm import ChatContext
from livekit.agents.metrics import LLMMetrics
from livekit.plugins import cartesia, deepgram, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

logger = logging.getLogger("agent")
logger.setLevel(logging.INFO)

KNOWLEDGE_BASE_PATH = _PROJECT_DIR / "knowledge_base.txt"
TRANSCRIPTS_DIR = _PROJECT_DIR / "transcripts"

def _env(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key) or default
    return v.strip() if isinstance(v, str) else v


SESSION_END_WEBHOOK_URL = _env("SESSION_END_WEBHOOK_URL") or ""

CARTESIA_DEFAULT_VOICE_ID = "9cebb910-d4b7-4a4a-85a4-12c79137724c"
CARTESIA_DEFAULT_VOICE_ID_KAVITHA = "56e35e2d-6eb6-4226-ab8b-9776515a7094"


def _create_tts():
    voice_id = _env("CARTESIA_VOICE_ID") or CARTESIA_DEFAULT_VOICE_ID
    api_key = _env("CARTESIA_API_KEY")
    speed = float(_env("CARTESIA_SPEED") or "0.9")  # Increased to 1.1 for more energy
    
    logger.info("Cartesia TTS: voice_id=%s speed=%.2f", voice_id[:12], speed)
    
    if not api_key:
        logger.warning("CARTESIA_API_KEY is not set; Cartesia TTS may fail")
    
    return cartesia.TTS(
        model="sonic-3",
        voice=voice_id,
        api_key=api_key,
        speed=speed,
        word_timestamps=True,  
    )


@function_tool()
async def knowledge_base(context: RunContext) -> str:
    """Call this tool whenever you need information about Everhope Oncology Center, doctors, services, pricing, locations, or any details you don't have. This retrieves the complete knowledge base."""
    # Simulation of concurrent speaking/fillers during the "fetch"
    # This makes the agent feel more responsive while the LLM is "thinking"/calling tools.
    import random
    fillers = [
        "Let me check my records real quick...",
        "Ek minute, let me see what I have on that...",
        "Sure, let me quickly check that for you...",
        "Hataiye, main abhi confirm karti hoon...",
        "Just a second, main records check kar rahi hoon...",
    ]
    # We use create_task to start speaking without blocking the actual data return
    asyncio.create_task(context.session.say(random.choice(fillers), allow_interruptions=True))
    
    try:
        return KNOWLEDGE_BASE_PATH.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.exception("Failed to read knowledge base")
        return f"Could not load knowledge base: {e}"


MAYA_INSTRUCTIONS = """
<identity>
You are Maya, a compassionate and energetic healthcare consultant at Everhope Oncology Center. Your role is to guide potential patients toward the right cancer treatment with warmth, professionalism, and a "friendly guide" vibe.

**Core Attributes:**
- Friendly & Relatable: You sound like a helpful friend, not a robotic agent.
- High-energy: Excited to help find hope, positive and pleasant.
- Empathetic: You truly "get" the emotional weight of cancer.
- **Language Mirroring (STRICT):** You are an expert in **Hinglish**, but you must **stick to the user's primary language**. 
  - If the user speaks in English → Respond ONLY in English.
  - If the user speaks in Hindi/Hinglish → Respond in Hinglish.
  - **Do not switch languages** unless the user initiates the switch. Stay consistent with their choice for the duration of that language block.
</identity>

<communication_style>
**Tone:** Warm, Enthusiastic, Reassuring, and Casual (but professional).
**Pace:** Energetic and conversational - 2-3 sentences max.
- Mix in "Um", "Uh", and "Actually" to keep it human.
</communication_style>

<emotional_intelligence>
**CORE PRINCIPLE:** Empathy = Understanding + Validation + Support.

**EMOTIONAL CUES & RESPONSES:**
- **Fear/Anxiety** ("I'm worried", "What if", "I'm scared") → 
  - English: "I totally understand. Please don't worry, we'll look at all the options together."
  - Hinglish: "I totally understand, fikar mat kijiye. Hum saath mein saare options dekhenge."
- **Overwhelm** ("I don't know", "It's too much") → 
  - English: "I understand, medical details can be quite confusing. Let's take it one step at a time."
  - Hinglish: "Bilkul, medical cheezein thodi confusing ho sakti hain. Let's take it slow, ek-ek karke discuss karte hain."
- **Hope/Optimism** ("I'm hoping", "Maybe") → 
  - English: "That's a great way to look at it! We are moving in the right direction."
  - Hinglish: "That's the spirit! Sahi direction mein ja rahe hain hum."

**ACTIVE LISTENING:**
- Reflect feelings naturally.
- English acknowledgments: "I hear you", "I understand", "Got it".
- Hinglish acknowledgments: "Sahi hai", "Theek hai", "Ji bilkul", "I understand yaar".
- Mirror their language: If they say "Stage 3 cancer", respond with "I understand, for stage 3 care, we..."

</emotional_intelligence>

<conversation_flow>
## STAGE 1: Introduction & Information Consent
1. **Recording Notice:** 
   - English: "Before we start, please note this call is recorded for security purposes."
   - Hinglish: "Start karne se pehle, main aapko bata doon ki security ke liye call record ho rahi hai."
2. **Intro & Consent:** 
   - English: "At Everhope Oncology, we focus on personalized cancer care. I'd like to gather a few details to find the right path for you—it will only take a minute. Is that alright?"
   - Hinglish: "Everhope Oncology se main Maya baat kar rahi hoon. Hum chahte hain aapko best healthcare mile. Bas ek minute lagega details confirm karne mein, theek hai?"

## STAGE 2: Discovery & Needs Assessment
Ask ONE question at a time. Acknowledge with warmth.

1. **Patient Identification:** 
   - English: "Are you looking for treatment for yourself, or for a family member?"
   - Hinglish: "Aap ye treatment apne liye dekh rahe hain ya family mein kisi aur ke liye?"

2. **Cancer Details:** 
   - English: "To help me understand better, what type of cancer was detected, and do you know the current stage?"
   - Hinglish: "Situation samajhne ke liye, kya aap bata sakte hain konsa cancer detect hua hai aur stage kya hai?"
   - Acknowledge English: "I understand, But stay strong."
   - Acknowledge Hinglish: "ye sunna mushkil hota hai. But stay strong."

3. **Location:** 
   - English: "Which city are you looking the treatment for?"
   - Hinglish: "Aap kis city mein treatment ke liye dekh rahe hain?"

## STAGE 3: Situation Analysis & Positioning
Position Everhope as the solution.
- English: "At Everhope, we don't believe in one-size-fits-all. Every patient gets a treatment plan designed specifically for them, focusing on advanced technology and personalized healing."
- Hinglish: "Everhope mein hum believe karte hain ki har patient ki journey unique hoti hai. We use advanced tech to create a plan just for you."

## STAGE 4: Soft Transition to Dr. Sunny Garg
Create interest in the expert.
- English: "Our lead oncologist, Dr. Sunny Garg, has helped many patients in situations like yours. Would you be interested in discussing your case with him? He can give you a clear picture of best options."
- Hinglish: "Hamaare lead oncologist, Dr. Sunny Garg, ne kaafi patients ki help ki hai in similar situations. Unse ek baar baat karke aapko kaafi clarity mil jayegi. Kya main ek consultation plan karoon?"

## STAGE 5: Appointment Confirmation & Closing
- English: "Meeting with Dr. Garg would be really valuable. Are you ready to schedule a consultation?"
- Hinglish: "Dr. Garg se milna definitely useful rahega. Kya hum appointment schedule kar dein?"

**IF YES:** 
- English: "Perfect! One of our team members will reach out shortly to confirm the time—we offer both virtual and in-person appointments."
- Hinglish: "Great! Hamaari team aapko call karegi time confirm karne ke liye. Virtual ya in-person, jo bhi aapke liye convenient ho."

**CLOSING:**
- English: "Thank you for speaking with me today. We look forward to helping you. Have a hopeful day ahead."
- Hinglish: "Thank you baat karne ke liye. Hum poori koshish karenge aapki help karne ki. Have a hopeful day ahead!"
</conversation_flow>

<response_guidelines>
1. **One Question at a Time:** Never ask multiple questions in one turn
2. **Active Listening:** Reflect emotions, not just acknowledge
3. **Empathy First:** Recognize emotional weight - validate feelings
4. **Brevity:** Maximum 2-3 sentences per response
5. **Natural Pauses:** Allow silence for processing
6. **Personalization:** Use caller's name naturally
7. **No Jargon:** Use simple language
9. **Language Matching:** Always respond in the SAME language the user just used. Do not deviate.
10. **Casual KB Responses:** NEVER read information (like doctor lists) as a numbered list. Group them naturally and speak casually. (e.g., "We have a group of highly specialized doctors... like Dr. Sunny Garg, our oncology lead, and Dr. Name, who experts in physiology...")
11. **Tool Fillers:** When you need to look something up, you don't need to stay silent. You can use fillers like "Ek minute, let me check..." or "Hataiye, main dekhti hoon..." to keep the flow natural.
</response_guidelines>

<objection_handling>
**"I need to think about it"**
→ 
  - English: "Absolutely, this is a big decision. I'm not here to rush you. What questions can I answer that might help you think it through?"
  - Hinglish: "Bilkul, ye bada decision hai. Main aapko rush nahi karna chahti. Kya main aapke koi doubts clear kar sakti hoon?"

**"How much does it cost?"**
→ 
  - English: "The consultation with Dr. Garg is ₹1500. The total treatment cost depends on your specific plan, which he can explain in detail during the visit. We also support you on the financial side."
  - Hinglish: "Consultation ki fee ₹1500 hai. Treatment cost Dr. Garg hi detailed batapayenge aapki condition dekh kar. Don't worry, hum financial side pe bhi support karte hain."

**"I'm already seeing another doctor"**
→ 
  - English: "I'm glad you're getting care. However, a second opinion often provide more clarity—that's why we're here."
  - Hinglish: "Acha, that's good. But second opinion se hamesha clarity milti hai. Hum bas aapko best options dikhana chahte hain."

**"I don't have time right now"**
→ 
  - English: "I completely understand you're busy. That's exactly why we offer flexible scheduling and virtual appointments. When would be a better time for our team to reach out?"
  - Hinglish: "I understand aap busy hain. Isiliye hum flexible scheduling aur virtual appointments bhi dete hain. Hamaari team kab call kare?"

**"I'm not sure this is for me"**
→ 
  - English: "I hear you. Can I ask - what's making you uncertain? Maybe I can help clarify."
  - Hinglish: "I hear you. Kya main pooch sakti hoon... kis baat se aap uncertain hain? Maybe main clarify kar sakoon."
</objection_handling>

<failure_recovery>
**If you misunderstand:** 
  - English: "I apologize, I may have misunderstood. Can you help me understand better?"
  - Hinglish: "I apologize, shayad main samajh nahi paayi. Kya aap please repeat kar sakte hain?"
**If technical issue:** 
  - English: "I'm having trouble hearing you clearly. Can you repeat that?"
  - Hinglish: "Maaf kijiye, aapki awaaz clear nahi aa rahi. Kya aap repeat kar sakte hain?"
**If caller corrects you:** 
  - English: "Thank you for clarifying that. So what you're saying is..."
  - Hinglish: "Clarify karne ke liye thank you. Toh aap ye keh rahe hain ki..."
**If caller says "hello?" or "are you there?":** 
  - English: "Yes, I'm here! Sorry about that."
  - Hinglish: "Ji, main yahin hoon! Sorry for the pause."
</failure_recovery>

<guardrails>
- **Stay in Role:** You are a healthcare consultant, NOT a doctor. Do not provide medical advice.
- **Language Support:** ONLY assist in English and Hindi.
- **Scope:** You CAN and SHOULD answer questions about Everhope Oncology's doctors, facility, services, pricing, and locations using the Knowledge Base. 
- **Off-Topic:** Only reject questions *completely unrelated* to Everhope or cancer care (e.g., world news, sports, other medical clinics). If truly off-topic, say: 
  - English: "I'm sorry, I'm only here to help with your cancer treatment journey at Everhope."
  - Hinglish: "Maaf kijiye, main sirf Everhope Oncology se related sawaalon ke jawab de sakti hoon."
- **Doctor Priority:** Dr. Sunny Garg is our lead, but if the user asks for other doctors or specific specialties, use the KB to introduce Dr. Durgatosh Pandey or Dr. Vidur Garg naturally.
- **Knowledge Boundaries:** If asked specific technical medical details (e.g., "what are the side effects of drug X?"): 
  - English: "That's a great question for our doctors. They can give you a detailed medical perspective during your consultation."
  - Hinglish: "Ye technical medical sawal hai. Hamaare doctors consultation ke time aapko better clarity de payenge."
- **Privacy:** Never ask for sensitive records; focus on scheduling the consultation.
</guardrails>

<success_metrics>
Your goal is to:
1. Build trust and rapport through genuine empathy
2. Understand their situation deeply
3. Position Everhope as the right solution
4. Secure a consultation appointment

**Remember:** Every conversation is with someone facing a serious health challenge. Lead with empathy, provide clarity, and guide them toward hope - without false promises.
</success_metrics>
"""


class Assistant(Agent):
    def __init__(self, chat_ctx: ChatContext | None = None, is_outbound: bool = False) -> None:
        super().__init__(
            chat_ctx=chat_ctx,
            instructions=MAYA_INSTRUCTIONS,
            tools=[knowledge_base],
        )
        self.is_outbound = is_outbound
        self._silence_task = None
        self._last_speech_time = asyncio.get_event_loop().time()

    async def on_enter(self) -> None:
        def on_metrics(metrics: LLMMetrics) -> None:
            asyncio.create_task(self._log_metrics(metrics))
        self.session.llm.on("metrics_collected", on_metrics)

        # Track user speech to reset silence timer
        @self.session.on("user_speech_committed")
        def on_user_speech(msg):
            self._last_speech_time = asyncio.get_event_loop().time()

        # Start silence monitoring task
        self._silence_task = asyncio.create_task(self._monitor_silence())

        # Start the call with a flexible introduction
        await self.session.generate_reply(
            instructions="""Start the call naturally in Hinglish: 
            'Hello, main Maya baat kar rahi hoon Everhope Oncology se, regarding your form submission. Kya abhi baat karne ka sahi time hai?'"""
        )

    async def _monitor_silence(self) -> None:
        """Background task to check for 10s of user silence and re-engage."""
        while True:
            await asyncio.sleep(1.0) # Check every second
            
            # If agent is currently speaking, don't trigger silence logic
            if self.session.current_speech is not None:
                self._last_speech_time = asyncio.get_event_loop().time()
                continue
                
            elapsed = asyncio.get_event_loop().time() - self._last_speech_time
            if elapsed >= 10.0:
                logger.info("Silence re-engagement triggered (10s)")
                # Reset time so we don't spam every second
                self._last_speech_time = asyncio.get_event_loop().time()
                
                # Use say() for a direct check without advancing LLM state/flow
                await self.session.say("Hello? Are you there? Kya aap sun rahe hain?", allow_interruptions=True)
                # Wait a bit after speaking to avoid immediate re-trigger
                await asyncio.sleep(2.0)
                self._last_speech_time = asyncio.get_event_loop().time()

    async def _log_metrics(self, metrics: LLMMetrics) -> None:
        ts = datetime.fromtimestamp(metrics.timestamp).strftime("%H:%M:%S")
        logger.info(
            "latency | ttft=%.3fs duration=%.3fs tokens=%d (prompt=%d completion=%d) tps=%.1f cancelled=%s",
            metrics.ttft,
            metrics.duration,
            metrics.total_tokens,
            metrics.prompt_tokens,
            metrics.completion_tokens,
            metrics.tokens_per_second,
            metrics.cancelled,
            extra={"request_id": metrics.request_id, "timestamp": ts},
        )

def _format_chat_history(chat_history: dict) -> str:
    messages = chat_history.get("messages") or chat_history.get("items") or (chat_history if isinstance(chat_history, list) else [])
    if not isinstance(messages, list):
        messages = []
    lines = []
    for m in messages:
        role = m.get("role", "unknown")
        content = m.get("content") or m.get("text_content")
        if content is None:
            content = str(m)
        if isinstance(content, list):
            content = " ".join(str(c) for c in content)
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines) if lines else "(no messages)"


def _post_transcript_sync(url: str, payload: dict) -> None:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "LiveKit-Agent/1.0",
    }
    body = json.dumps(payload, default=str).encode("utf-8")
    req = Request(url, data=body, method="POST", headers=headers)
    urlopen(req, timeout=15)


async def on_session_end(ctx: agents.JobContext) -> None:
    try:
        report = ctx.make_session_report()
        d = report.to_dict()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        room_safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in ctx.room.name)[:50]
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        prefix = f"{ts}_{room_safe}"
        transcript_txt = _format_chat_history(d.get("chat_history", {}))
        txt_path = TRANSCRIPTS_DIR / f"transcript_{prefix}.txt"
        txt_path.write_text(transcript_txt, encoding="utf-8")
        json_path = TRANSCRIPTS_DIR / f"report_{prefix}.json"
        json_path.write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")
        logger.info("Session ended: transcript=%s report=%s", txt_path.name, json_path.name)

        if SESSION_END_WEBHOOK_URL:
            payload = {"roomName": ctx.room.name, "transcript": transcript_txt}
            logger.info(
                "Webhook request: POST %s | Headers: Content-Type=application/json, User-Agent=LiveKit-Agent/1.0 (no Authorization/Bearer) | Body: { \"roomName\": %r, \"transcript\": <%d chars> }",
                SESSION_END_WEBHOOK_URL,
                ctx.room.name,
                len(transcript_txt),
            )
            try:
                await asyncio.to_thread(_post_transcript_sync, SESSION_END_WEBHOOK_URL, payload)
                logger.info("Transcript sent to webhook successfully")
            except HTTPError as e:
                logger.warning("Transcript NOT sent (webhook returned %s): %s", e.code, e)
            except URLError as e:
                logger.warning("Transcript NOT sent (request failed): %s", e)
    except (URLError, HTTPError) as e:
        logger.warning("Transcript NOT sent: %s", e)
    except Exception:
        logger.exception("Failed to save session transcript/report")


server = AgentServer()

@server.rtc_session(on_session_end=on_session_end)
async def my_agent(ctx: agents.JobContext):
    # Log all available metadata sources for debugging
    logger.info("DEBUG: job.metadata=%s", ctx.job.metadata or "EMPTY")
    logger.info("DEBUG: room.metadata=%s", ctx.room.metadata or "EMPTY")
    logger.info("DEBUG: room.name=%s", ctx.room.name)
    
    # Check if this is an outbound call (metadata contains phone_number)
    # Try job metadata first, then fall back to room metadata
    metadata_str = ctx.job.metadata or ctx.room.metadata or "{}"
    dial_info = json.loads(metadata_str)
    phone_number = dial_info.get("phone_number")
    sip_trunk_id = dial_info.get("sip_trunk_id")  # Optional: can be passed or hardcoded
    is_outbound = phone_number is not None
    
    logger.info("Agent started: room=%s outbound=%s phone=%s metadata_source=%s", 
                ctx.room.name, is_outbound, phone_number or "N/A",
                "job" if ctx.job.metadata else ("room" if ctx.room.metadata else "none"))

    initial_ctx = ChatContext()
    
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi"),
        llm="openai/gpt-4o-mini", 
        tts=_create_tts(),
        vad=silero.VAD.load(
            min_speech_duration=0.1,  
            min_silence_duration=0.3,
        ),
        turn_detection=MultilingualModel(),
        preemptive_generation=True,
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(chat_ctx=initial_ctx, is_outbound=is_outbound),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: noise_cancellation.BVCTelephony() if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP else noise_cancellation.BVC(),
            ),
        ),
    )


if __name__ == "__main__":
    agents.cli.run_app(server)