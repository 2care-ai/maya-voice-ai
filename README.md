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

## Files

- `agent.py` - Main agent code
- `knowledge_base.txt` - Knowledge base for Everhope information
- `requirements.txt` - Python dependencies
