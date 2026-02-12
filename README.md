# LiveKit Agent Deployment

This agent handles voice calls with AI-powered conversation using Maya from Everhope Oncology Center.

## Features

- Bilingual support (English & Hindi)
- Voice-to-voice conversation
- Automatic call recording and transcription
- SIP/phone call support

## Environment Variables Required

- `LIVEKIT_URL` - Your LiveKit server URL
- `LIVEKIT_API_KEY` - LiveKit API key
- `LIVEKIT_API_SECRET` - LiveKit API secret
- `GLADIA_API_KEY` - Gladia STT API key
- `CARTESIA_API_KEY` - Cartesia TTS API key
- `CARTESIA_VOICE_ID` - (Optional) Custom Cartesia voice ID
- `OPENAI_API_KEY` - OpenAI API key for LLM
- `SUPERMEMORY_API_KEY` - (Optional) Super Memory API key for agent memory/context. If set, the agent searches Super Memory on each user turn and injects relevant context into the reply.
- `SUPERMEMORY_CONTAINER_TAG` - (Optional) Scope search to a project/user (e.g. `sm_project_livekit101`). Get project IDs from [Supermemory console](https://console.supermemory.ai).

## Deployment

### Local Development

```bash
uv run agent.py dev
```

### Production

```bash
python agent.py start
```

### Call recording (Room Composite Egress)

Recording is started by **the outbound call script**, not the agent. When you run `make_outbound_call.py`, it creates the room, **starts room composite egress** (recording to S3), then creates the SIP participant. The recording runs for the life of the room and uploads when the room ends.

**To test:** Run `uv run python make_outbound_call.py <phone> <sip_trunk_id>`. You should see `Call recording started (egress_id=...)` in the script output. After the call ends (hang up), check S3: bucket `S3_BUCKET_NAME`, folder `S3_RECORDINGS_FOLDER`, file `{room_name}-{time}.ogg`. Ensure S3 env vars are set in `.env` (see DEPLOY.md).

## Super Memory integration

The agent uses [Supermemory](https://supermemory.ai) so replies can use stored memories and documents (low-latency RAG-style context).

**What was added:**

1. **`supermemory_helper.py`** – Async helper that calls the Super Memory API:
   - Uses `AsyncSupermemory` and `SUPERMEMORY_API_KEY` from env.
   - `search_memories(query, container_tag=...)` runs a **hybrid** search (memories + document chunks) with a 4s timeout so voice stays responsive.
   - Returns a single string of relevant snippets for the LLM.

2. **`agent.py`** – Hook into the turn pipeline:
   - **`on_user_turn_completed(self, turn_ctx, new_message)`** runs when the user finishes speaking (same moment preemptive LLM/TTS can start).
   - It takes the user’s transcript (`new_message.text_content`), calls `search_memories(query)`.
   - If anything is returned, it injects it into `turn_ctx` as an assistant message: *"Relevant context from memory (use only if it helps answer): …"*.
   - The LLM then generates its reply with that context in the same turn (no extra tool round-trip).

**Flow:** User speaks → STT final transcript → `on_user_turn_completed` runs → Super Memory search (in parallel with preemptive generation) → context injected → LLM reply uses it.

**Adding your document and testing speed:**

1. **Ingest the knowledge base** (one-off or when the file changes):
   ```bash
   uv run python ingest_document_to_supermemory.py
   ```
   Uses `knowledge_base.txt` by default, or pass a path: `uv run python ingest_document_to_supermemory.py path/to/file.txt`.  
   Uses `SUPERMEMORY_API_KEY` and `SUPERMEMORY_CONTAINER_TAG` (default `sm_project_livekit101`) from `.env`. Re-run to update the same document (same `custom_id`).

2. **Test search speed manually:**
   ```bash
   uv run python test_supermemory_speed.py
   ```
   Runs a few test queries and prints latency (ms) and result snippet. Optional: pass a custom query: `uv run python test_supermemory_speed.py "your question"`.

3. **Agent:** Ensure `SUPERMEMORY_CONTAINER_TAG` in `.env` matches the tag used in step 1 so the agent searches the same project.

## Files

- `agent.py` - Main agent code
- `supermemory_helper.py` - Super Memory search helper (used by agent)
- `knowledge_base.txt` - Knowledge base for Everhope information
- `requirements.txt` - Python dependencies
