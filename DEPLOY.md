# Deploy agent to LiveKit Cloud

## Prerequisites

- [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/) installed
- [LiveKit Cloud](https://cloud.livekit.io) project
- This project runs locally (e.g. `uv run agent.py console`)

## 1. Install LiveKit CLI

**Windows**

- **Option A (recommended):** In **PowerShell** or **CMD** (not Git Bash), run:
  ```powershell
  winget install LiveKit.LiveKitCLI
  ```
  Then close and reopen your terminal (or Git Bash). If `lk` still isn’t found in Git Bash, use Option B or run `lk` from PowerShell/CMD.

- **Option B (manual):** Download the Windows binary from [livekit-cli releases](https://github.com/livekit/livekit-cli/releases/latest) (e.g. `livekit-cli_*_windows_amd64.zip`). Unzip, put `lk.exe` somewhere in your PATH (or a folder you use), then run `lk` from that folder or any shell that has it on PATH.

- **If `lk` is not found after winget install:** The CLI is installed but its folder is often not on PATH. Use the full path (replace `gsbal` with your username if needed):
  ```powershell
  & "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\LiveKit.LiveKitCLI_Microsoft.Winget.Source_8wekyb3d8bbwe\lk.exe" cloud auth
  ```
  Or add that folder to your user PATH: **Settings → System → About → Advanced system settings → Environment Variables → User variables → Path → Edit → New** → paste:
  `%LOCALAPPDATA%\Microsoft\WinGet\Packages\LiveKit.LiveKitCLI_Microsoft.Winget.Source_8wekyb3d8bbwe`
  Then restart PowerShell.

**macOS:**

```bash
brew install livekit-cli
```

**Linux:**

```bash
curl -sSL https://get.livekit.io/cli | bash
```

## 2. Link your project

From the project root:

```bash
lk cloud auth
```

This opens a browser to sign in and link a LiveKit Cloud project. To pick a different project later:

```bash
lk project list
lk project set-default "<project-name>"
```

## 3. Deploy the agent

**First time (register + deploy):**

```bash
lk agent create
```

This will:

- Create `livekit.toml` and a Dockerfile if missing
- Register the agent with your Cloud project
- Build a container image and deploy it

**Later (update after code changes):**

```bash
lk agent deploy
```

`.env` and `.env.*` are not uploaded; use **secrets** for production (see below).

## 4. Secrets (API keys in production)

Your agent needs `LIVEKIT_*` (handled by Cloud), plus any provider keys (e.g. OpenAI, AssemblyAI, Cartesia). Push them as secrets:

```bash
lk agent secrets set --from-file .env
```

Or set individually:

```bash
lk agent secrets set OPENAI_API_KEY=sk-...
lk agent secrets set ASSEMBLYAI_API_KEY=...
```

Secrets are available as env vars in the deployed agent. Do **not** commit `.env`; keep it in `.gitignore`.

**Room Composite Egress (call recordings to S3):** If you use egress, set `S3_BUCKET_NAME`, `S3_RECORDINGS_FOLDER`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_REGION`. Optional for S3-compatible storage: `AWS_ENDPOINT`, `AWS_FORCE_PATH_STYLE=true`. Layout is `single-speaker`; recordings appear under the folder prefix with `{room_name}-{time}.ogg` after the room ends. Verify by running a test call (e.g. `make_outbound_call.py`), ending the room, then checking the bucket.

## 5. Monitor

```bash
lk agent status
lk agent logs
```

- **Status:** replicas, health
- **Logs:** live tail of agent stdout (including your latency logs)

## 6. Use the deployed agent

Once deployed, the agent is **on** for your project. You can use it from:

1. **Agent Playground**  
   In [LiveKit Cloud](https://cloud.livekit.io) → your project → **Playground**. Join a room; the agent will attach (if dispatch is default).

2. **Your own frontend**  
   - Create a room via [Room API](https://docs.livekit.io/reference/other/roomservice-api/) or your backend.
   - Issue an [access token](https://docs.livekit.io/frontends/authentication/tokens/) for the client.
   - Client connects with a LiveKit SDK (e.g. [React](https://docs.livekit.io/transport/sdk-platforms/react/), [Swift](https://docs.livekit.io/transport/sdk-platforms/swift/)); the agent joins when configured (e.g. auto-dispatch).

3. **Telephony**  
   Configure a [phone number](https://docs.livekit.io/telephony/start/phone-numbers/) and [dispatch rule](https://docs.livekit.io/telephony/accepting-calls/dispatch-rule/) so inbound calls are routed to your agent.

## 7. Rebuild and rollback

- **Rebuild:** change code, then `lk agent deploy`.
- **Rollback:** use the Cloud dashboard to revert to a previous deployment (if available), or redeploy an older commit.

## Reference

- [Deploy agents (overview)](https://docs.livekit.io/deploy/agents/)
- [Get started (step-by-step)](https://docs.livekit.io/deploy/agents/start/)
- [Secrets](https://docs.livekit.io/deploy/agents/secrets/)
- [Builds and Dockerfiles](https://docs.livekit.io/deploy/agents/builds/)
