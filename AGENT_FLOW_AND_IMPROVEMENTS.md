# Maya Agent: Flow, Config & Improvement Guide

## 1. Complete flow and config

### High-level pipeline

```
Room (SIP/WebRTC) → Audio input → Noise cancellation → VAD → STT (Deepgram) → Turn detection → LLM → TTS (Cartesia) → Audio output → Room
```

### Entry and session lifecycle

1. **Job start** (`my_agent`)
   - `JobContext` receives room + optional metadata (`phone_number`, `sip_trunk_id`).
   - Parses metadata to detect outbound vs inbound.
   - Builds `ChatContext`, `AgentSession`, and `Assistant` agent.

2. **Session start**
   - `session.start(room, agent, room_options)` connects the session to the room.
   - Room I/O subscribes to participant(s); by default first participant (or SIP) is linked.
   - Audio input uses `AudioInputOptions` with conditional noise cancellation (BVC vs BVCTelephony for SIP).

3. **Audio input path**
   - **Noise cancellation**: `noise_cancellation.BVCTelephony()` for SIP participants, `noise_cancellation.BVC()` otherwise.
   - **VAD**: Silero with `min_speech_duration=0.1`, `min_silence_duration=0.3`.
   - **STT**: Deepgram Nova-3, `language="multi"` (multilingual).
   - **Turn detection**: `MultilingualModel()` (context-aware turn detector on top of VAD/STT).

4. **Agent logic**
   - **Assistant** extends `Agent` with Maya instructions, `knowledge_base` tool, and optional outbound flag.
   - **Preemptive generation**: `True` — LLM/TTS can start before end-of-turn is committed (reduces perceived latency; may waste compute on interrupts).
   - **Silence re-engagement**: Custom `_monitor_silence()` checks every 1s; after 10s of no user speech (and agent not speaking), says “Hello? Are you there?…” and resets timer.

5. **Output path**
   - **LLM**: `openai/gpt-4o-mini`.
   - **TTS**: Cartesia Sonic-3, configurable voice/speed, `word_timestamps=True`.
   - Agent speech is interruptible by default; tool fillers use `say(..., allow_interruptions=True)`.

6. **Session end**
   - `on_session_end`: builds session report, saves transcript + JSON report to `transcripts/`, optionally POSTs transcript to `SESSION_END_WEBHOOK_URL`.

### Main config snapshot

| Component        | Current setting |
|-----------------|-----------------|
| STT             | Deepgram Nova-3, `language="multi"` |
| VAD             | Silero: `min_speech_duration=0.1`, `min_silence_duration=0.3` |
| Turn detection  | `MultilingualModel()` |
| LLM             | `openai/gpt-4o-mini` |
| TTS             | Cartesia Sonic-3, speed from env (default 0.9), `word_timestamps=True` |
| Preemptive gen  | `True` |
| Noise cancel    | BVC or BVCTelephony (SIP) |
| Silence re-engage | 10s no user speech → “Hello? Are you there?” |

---

## 2. Where to improve

### Call quality

- **Noise cancellation**: You already use BVC/BVCTelephony per participant kind; ensure LiveKit Cloud “Enhanced noise cancellation” is enabled in the project so the best available model is used.
- **Audio codec / transport**: Handled by LiveKit; for SIP, ensure your trunk and LiveKit region are aligned to minimize packet loss and jitter.
- **TTS quality**: Cartesia Sonic-3 is solid; you can tune `speed` (e.g. 0.95–1.0 for clarity) and optionally `volume`/`emotion` if the plugin supports it for more natural delivery.

### Voice detection (VAD)

- **Current**: `min_speech_duration=0.1`, `min_silence_duration=0.3` — quite aggressive (fast reaction, may chop or false-trigger).
- **Tuning**:
  - **Fewer false “speech” triggers**: Increase `min_speech_duration` (e.g. 0.15–0.2) and/or `activation_threshold` (default 0.5; try 0.55–0.6 in noisy environments).
  - **Wait longer before “user finished”**: Increase `min_silence_duration` (e.g. 0.4–0.5) so slow speakers aren’t cut off; default in docs is 0.55.
- **Prewarm**: Load Silero (and turn detector) in a **prewarm/setup** function and pass into `AgentSession` so the first call doesn’t pay model load time; also run `download-files` in Docker/build so weights are in the image.

### Silence handling

- **Current**: Custom 10s timer + “Hello? Are you there?” is good for keeping the line alive.
- **Improvements**:
  - **Session-level**: Consider `user_away_timeout` (default 15s) to mark user “away” and optionally adapt behavior (e.g. shorter re-engagement phrase or different messaging).
  - **Re-engagement**: Avoid firing re-engagement while the agent is still speaking (you already skip when `current_speech is not None`) and consider a short cooldown after any agent utterance so you don’t prompt immediately after the agent finishes.
  - **SIP/telephony**: For outbound, 10s might be right; for inbound, you could make the threshold or message configurable (e.g. env).

### Latency

- **Preemptive generation**: Already `True`; good for time-to-first-byte. Monitor cost vs benefit if users interrupt often.
- **LLM**: gpt-4o-mini is fast; if you need even lower latency and can accept a smaller model, consider a faster option; otherwise keep and rely on preemptive + streaming.
- **STT**: Nova-3 is low-latency; if most users are in one region (e.g. India), check Deepgram/LiveKit Inference regional endpoints (e.g. Mumbai) to reduce round-trip.
- **TTS**: Cartesia streaming is already used; `word_timestamps=True` is good for aligned transcripts if you enable `use_tts_aligned_transcript` for the frontend.
- **Cold start**: Prewarm VAD and turn detector; run `download-files` in Docker so no first-call model download. Optionally prewarm STT/TTS/LLM if your stack supports it.

### Response quality

- **Instructions**: Maya’s instructions and guardrails are clear (language mirroring, empathy, one question at a time, no medical advice). Refine from real transcripts (e.g. in `transcripts/`).
- **Tool use**: `knowledge_base` with filler phrases improves perceived responsiveness; ensure the tool returns concise, relevant snippets so the LLM doesn’t read long blocks.
- **Brevity**: Instructions already say 2–3 sentences max; you can add a short “max_sentences” or “be concise” line in the system block if the model sometimes over-explains.
- **Turn detection**: MultilingualModel helps end-of-turn accuracy; if you see mid-sentence cuts or long pauses before reply, tune `min_endpointing_delay` / `max_endpointing_delay` (and VAD `min_silence_duration`) together.

### Interruptions and false interrupts

- **Current**: Defaults allow interruptions; you didn’t set `min_interruption_duration` or `min_interruption_words`.
- **If users get cut off by noise**: Increase `min_interruption_duration` (e.g. 0.6–0.7) or `min_interruption_words` (e.g. 1–2) so brief noise doesn’t interrupt.
- **False interruption**: `resume_false_interruption=True` and `false_interruption_timeout=2.0` (default) resume agent speech if no real user words are seen; keep or tune timeout if needed.

---

## 3. Quick wins (summary)

1. **Prewarm** VAD and turn detector; run **download-files** in Docker.
2. **Tune VAD**: Slightly higher `min_silence_duration` (e.g. 0.4–0.5) and optionally `activation_threshold` if you have noise or false triggers.
3. **STT region**: Use a Deepgram/LiveKit region close to your users (e.g. India) if available.
4. **Session options**: Explicitly set `min_interruption_duration` and/or `min_interruption_words` if you see false interrupts; optionally set `user_away_timeout` and align with your 10s re-engagement.
5. **Observability**: You already log LLM metrics (ttft, duration, tokens); add VAD/STT metrics if available to debug latency and turn boundaries.

This document reflects the flow and config in `agent.py` and LiveKit Agents docs; adjust thresholds and timeouts based on real call logs and transcripts.
