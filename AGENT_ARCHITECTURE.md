# Maya agent – how it works

## High-level

1. **Session start** → `AgentSession` runs with STT (Deepgram), LLM (Groq), TTS (ElevenLabs), VAD (Silero).
2. **Lead context**: Diagnostic labs (cancer-positive reports). Calls are from the patient support team regarding the patient’s recent medical test. **Non-disclosure**: do not mention cancer/positive report/diagnosis unless the user has already discussed the report with their doctor.
3. **Assistant** loads Maya’s system prompt from `maya-prompt.py` and, in `on_enter`, runs **OpeningTask** first, then **branches** by `good_time`:
   - **If good_time**: TaskGroup(Confirmation → Diagnosis → Treatment → Timeline → Financial → Geography → Closing).
   - **If not good_time**: TaskGroup(ScheduleCallback → Closing).
4. **After the flow** → one final “flow complete” reply, then the agent responds warmly (base Maya + global `get_center_info` if needed).
5. **Session end** → transcript and flow results sent to webhook.

---

## Entry: `agent.py` → `my_agent`

- Triggered when a participant joins and the job uses `agent_name="maya-agent"`.
- Reads **metadata** from job/room: `phone_number`, `patient_name` (optional), `lead_source` (optional).
- Builds **AgentSession** (STT: Deepgram nova-3, LLM: Groq, TTS: ElevenLabs, VAD: Silero, turn detection, preemptive generation).
- Creates **Assistant(chat_ctx=ChatContext(), patient_name=patient_name)**, stores it in `_room_agents[room.name]`, starts the session (BVC noise cancellation for SIP).

---

## Assistant (main Agent)

- **Instructions**: `AGENT_INSTRUCTIONS` from `maya-prompt.py` (compact) – diagnostic-lab context, empathy, **non-disclosure** when report not discussed, one question per turn.
- **Global tool**: `get_center_info` for post-flow or when a task doesn’t expose it.

**`on_enter`:**

1. Metrics logging, user_speech for silence, **silence monitor** (10s → “Are you still there?”).
2. **Opening TaskGroup** (single task): `OpeningTask(patient_name=self._patient_name)` → capture `good_time`.
3. **Branch**:
   - **good_time True**: TaskGroup(Confirmation, Diagnosis, Treatment, Timeline, Financial, Geography, Closing with `is_callback_path=False`).
   - **good_time False**: TaskGroup(ScheduleCallback, Closing with `is_callback_path=True`).
4. Merge opening + branch results into `_flow_results` (for webhook).
5. Log task_result keys, then `generate_reply` “Flow complete. Respond warmly and briefly.”

---

## System prompt and context

- One shared **ChatContext** for the whole call. Opening runs first; then the chosen TaskGroup reuses the same context.
- **System prompt** is **replaced** when each task starts (task’s instructions), not appended. **generate_reply(instructions="…")** is one-off for that reply and not stored in history.
- After each TaskGroup, `summarize_chat_ctx=True` summarizes the group’s conversation into one message.

---

## Task list (diagnostic-lab flow)

| Order | Id               | Task                        | Purpose |
|-------|------------------|-----------------------------|--------|
| 1     | opening          | OpeningTask(patient_name)   | Greet with patient name, “patient support team regarding your recent medical test”, ask good time → `record_opening_done(good_time)` |
| 2a    | confirmation     | ConfirmationTask            | “Have you discussed the report with your doctor?” If **not** → say only non-disclosure line (no cancer/diagnosis). → `record_confirmation(aware, summary)` |
| 3a    | diagnosis        | DiagnosisQualificationTask  | Cancer type, biopsy done?, stage known?, metastasis known? → `record_diagnosis(...)` |
| 4a    | treatment        | TreatmentStatusTask         | Started? Hospital? Surgery planned? Chemo advised? → `record_treatment(...)` |
| 5a    | timeline         | DecisionTimelineTask        | When they plan to start/look at treatment → `record_timeline(timeline=what they said)` stored as string |
| 6a    | geography        | GeographyTask               | "Where are you from?" then "Willing to travel if needed?" → `record_geography(where_from, willing_to_travel_answer)` stored as strings |
| 7a    | closing          | ClosingTask(is_callback_path=False) | Thank, next steps → `close_call()` |
| 2b    | schedule_callback| ScheduleCallbackTask        | (When good_time=False) Ask when to call back → `record_callback(...)` |
| 3b    | closing          | ClosingTask(is_callback_path=True)  | “We’ll call back at [preference]” → `close_call()` |

**Main path**: opening → confirmation → diagnosis → treatment → timeline → geography → closing.  
**Callback path**: opening → schedule_callback → closing.

---

## Metadata (patient_name, lead_source)

- **patient_name**: Used in OpeningTask for “May I speak with [name]?” (no Mr./Ms.). Passed from room/job metadata; outbound call scripts can include it when creating the room.
- **lead_source**: Optional (e.g. `"diagnostic_lab"`); can be used for analytics or routing; agent currently logs and can pass to Assistant if needed.

---

## Session end / webhook

- **on_session_end**: POST to `SESSION_END_WEBHOOK_URL` with `roomName`, `transcript`, and **flowResults**.
- **flowResults** keys: always `opening`; then either `confirmation`, `diagnosis`, `treatment`, `timeline`, `geography`, `closing` (main path) or `schedule_callback`, `closing` (callback path).

---

## Spoken output vs internal instructions

- **Problem**: The LLM must not speak internal text (e.g. "Next step: ...", "Say:", task descriptions). Everything it outputs goes to TTS.
- **Fix**: (1) Base and task instructions require output to be only the words the user should hear—no labels, instructions, or meta. (2) `generate_reply(instructions=...)` is phrased as "Output only the words to speak (no labels): ...". (3) When a tool returns a prescribed line (e.g. non-disclosure script from `record_confirmation(aware=False)`), task instructions tell the LLM to reply with exactly that line. (4) **Opening** uses `session.say(script)` so the first utterance is scripted and avoids LLM cold start. No post-processing or TTS filtering; behaviour is enforced via prompts only.

## Latency / cold start

- **First utterance**: Opening uses `session.say()` so the first line is scripted and does not wait on LLM; TTS may still have first-request latency (provider-dependent).
- Each task uses only that task’s instructions as system prompt (fewer input tokens). Conversation history grows in one shared context. After each TaskGroup, context is summarized so the next step doesn’t carry the full transcript. **generate_reply(instructions=…)** does not add to stored context.
