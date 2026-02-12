# Maya Prompt Optimization Plan

## Current state

| Metric | Value |
|--------|--------|
| **Characters** | ~10,116 |
| **Words** | ~1,575 |
| **Estimated tokens** | **~2,529** (chars ÷ 4) |

The full `maya-prompt.py` is sent as the agent system instructions. For voice, that’s a large system prompt: it increases latency (more input tokens per request) and leaves less context for conversation history.

---

## Optimal setup for voice

- **System prompt:** **400–800 tokens** for core behavior. Enough for identity, output rules, and guardrails; not full scripts.
- **Max output tokens:** **80–150** per turn. Voice should be 1–3 short sentences; 150 tokens is a safe cap and keeps TTFT and TTS latency low.
- **Strategy:** Put only “always-on” rules in the system prompt. Move stage scripts, long examples, and objection handlers into a **compact reference** or **dynamic injection** (e.g. by stage) so the base prompt stays small.

---

## What to keep in the prompt (core, always-on)

Keep these in the **main system instructions** (target ~500–700 tokens):

1. **Identity (short)**  
   - Name, role (Maya, healthcare consultant, Everhope).  
   - One line: friendly, empathetic, high-energy.  
   - **Language rule:** Mirror user language (English ↔ Hinglish); do not switch unless they do.

2. **Output / communication rules**  
   - 2–3 sentences max per reply.  
   - One question at a time.  
   - Plain text only; TTS-friendly (no lists/markdown/emojis).  
   - Light fillers (“Um”, “Actually”) for naturalness.

3. **Emotional intelligence (compact)**  
   - Single principle: “Empathy = understand + validate + support.”  
   - One line each for: fear, overwhelm, hope.  
   - “Reflect feelings; use ‘I hear you’ / ‘Sahi hai’ etc. by language.”

4. **Guardrails (short)**  
   - Consultant only; no medical advice.  
   - English + Hindi only.  
   - Scope: Everhope doctors, facility, services, pricing, locations.  
   - Off-topic / technical medical → deflect to doctor/consultation.  
   - No sensitive records; focus on scheduling.

5. **Success goal (one line)**  
   - Build trust, understand situation, position Everhope, secure consultation.

---

## What to take out or move

- **Full stage-by-stage scripts (conversation_flow)**  
  - **Why:** Long, repetitive, many duplicate EN/HI lines.  
  - **Move to:** Short “stage checklist” in the prompt (e.g. “1. Recording notice 2. Intro & consent 3. Discovery (who, cancer type/stage, city) 4. Position Everhope 5. Dr. Garg + book consultation”) or inject by stage via `generate_reply(instructions=…)` so only the current step is in context.

- **Every English + Hinglish example pair**  
  - **Why:** Doubles size; model can mirror from one example.  
  - **Action:** Keep **one** example per idea (e.g. one “fear” response in each language), or only “Respond in the same language as the user” and drop most scripted lines.

- **Full objection_handling block**  
  - **Why:** ~400+ tokens of canned replies.  
  - **Move to:** 2–3 short rules: “If cost → mention ₹1500 consultation, rest in visit. If second opinion → say we’re here for clarity. If no time → offer flexible/virtual and ask when to call back.” Let the model generate the exact phrasing.

- **Full failure_recovery block**  
  - **Why:** Rare; adds tokens every turn.  
  - **Action:** One line: “If you mishear or they say ‘hello?’: apologize briefly and ask to repeat or confirm you’re there.”

- **success_metrics paragraph**  
  - **Why:** Redundant with “Success goal” above.  
  - **Action:** Merge into the one-line goal.

---

## Implementation strategy

1. **Add `max_completion_tokens`**  
   - Set Groq LLM `max_completion_tokens=120` (or 80–150) so every reply is capped and latency stays predictable.

2. **Create a “compact” Maya prompt**  
   - Implemented as `get_maya_instructions_compact()` in `maya-prompt.py`.  
   - **~579 tokens** (chars÷4), within 500–700 target. Markdown sections: Identity, Output rules, Conversational flow, Emotional intelligence, Key responses, Guardrails, Goal.

3. **Use compact prompt as default system instructions**  
   - Agent loads it via `_load_maya_instructions(compact=True)`; set `compact=False` to use the full prompt.

4. **Optional: stage-specific instructions**  
   - When you have conversation state (e.g. “stage 2 – discovery”), call `generate_reply(instructions="Current step: ask for cancer type and stage. One question only.")` so the model gets minimal, relevant guidance without putting all stages in the base prompt.

5. **Keep full maya-prompt.py as reference**  
   - Use it for training, docs, or human reference; don’t load the full text into the LLM every turn.

6. **Re-measure after changes**  
   - Re-run a token counter on the compact prompt (e.g. chars÷4 or tiktoken) and confirm it’s in the 500–700 token range.

---

## Summary

| Item | Action |
|------|--------|
| **System prompt size** | Reduce from ~2,529 to **500–700 tokens** |
| **Max output tokens** | Set **80–150** (e.g. 120) on Groq LLM |
| **Keep** | Identity, output rules, short empathy rules, guardrails, one-line goal |
| **Remove / move** | Full stage scripts, duplicate EN/HI pairs, long objection/failure blocks |
| **Optional** | Stage-based `generate_reply(instructions=…)` for current step only |

This keeps latency low, leaves room for dialogue history, and preserves Maya’s behavior by focusing the prompt on rules and boundaries rather than verbatim scripts.
