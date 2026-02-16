# Self-hosting the LiveKit agent on AWS EC2

This guide walks through running the agent on your own EC2 instance while keeping LiveKit Cloud as the room server. The agent connects to your LiveKit Cloud project; no need to run a self-hosted LiveKit server.

---

## Overview

| Component        | Where it runs              |
|-----------------|----------------------------|
| LiveKit server  | LiveKit Cloud (unchanged)  |
| Agent worker    | Your EC2 instance (Docker) |
| Outbound calls  | Triggered from EC2 or your PC |

You need: an EC2 instance, the project files and `.env` on the server, and Docker to run the agent container.

---

## Prerequisites

- **AWS account** and ability to launch EC2 instances
- **LiveKit Cloud project** with `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` in `.env`
- **PEM key pair** downloaded when creating the EC2 instance
- **Public IP** of the EC2 instance (e.g. `54.175.41.71`)
- **SSH client** (OpenSSH on Windows 10/11, or PowerShell)

---

## 1. EC2 instance setup

### 1.1 Launch instance

- **AMI:** Ubuntu Server 22.04 LTS (or 24.04)
- **Instance type:** e.g. `t3.small` or `t3.medium` (voice agents benefit from a bit of CPU)
- **Key pair:** Create or select a key pair and download the `.pem` file. Store it securely (e.g. in the project folder or `~/.ssh`).
- **Security group:** Allow SSH (port 22) from your IP. Do not open 22 to `0.0.0.0/0` in production unless required.

### 1.2 Connect

From your local machine (replace paths and IP):

```powershell
ssh -i D:\path\to\your-key.pem ubuntu@YOUR_PUBLIC_IP
```

**Windows – PEM permissions:** If you see `WARNING: UNPROTECTED PRIVATE KEY FILE!` or `bad permissions`, fix the key so only your user can read it.

In **PowerShell (Run as Administrator):**

```powershell
$keyPath = "D:\path\to\your-key.pem"
$acl = Get-Acl $keyPath
$acl.SetAccessRuleProtection($true, $false)
$acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) }
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule($env:USERNAME, "Read", "Allow")
$acl.AddAccessRule($rule)
Set-Acl $keyPath $acl
```

Then run the `ssh` command again.

---

## 2. Upload project files to EC2

From your **local project root** (e.g. `D:\livekit-101`).

### 2.1 Upload main files (recursive)

```powershell
scp -i D:\path\to\your-key.pem -r * ubuntu@YOUR_PUBLIC_IP:~/livekit-agent
```

This creates `~/livekit-agent` on the server and copies all non-hidden files. Hidden files (e.g. `.env`) are **not** included by `*` on Windows.

### 2.2 Upload `.env` separately

Required for the agent and for running `make_outbound_call.py` on the server.

```powershell
scp -i D:\path\to\your-key.pem D:\livekit-101\.env ubuntu@YOUR_PUBLIC_IP:~/livekit-agent/
```

**Security:** Prefer creating `.env` once on the server (e.g. paste from a secrets manager) instead of re-uploading from a dev machine. Do not commit `.env` to git.

### 2.3 Optional: upload other dotfiles

If you use `.env.production` or similar, upload them explicitly:

```powershell
scp -i D:\path\to\your-key.pem D:\livekit-101\.env.production ubuntu@YOUR_PUBLIC_IP:~/livekit-agent/
```

---

## 3. Fix `.env` for Docker (if needed)

Docker’s `--env-file` does not allow spaces in variable names. Ensure no spaces around `=` and no duplicate keys with different spacing.

**Invalid (will fail):**

```env
ELEVEN_LABS_DEFAULT_VOICE_ID = "r0CJZmLNXYNjo1eAJ2nq"
```

**Valid:**

```env
ELEVEN_LABS_DEFAULT_VOICE_ID="r0CJZmLNXYNjo1eAJ2nq"
```

On the server, edit if needed:

```bash
nano ~/livekit-agent/.env
```

Remove duplicate lines and any spaces around `=`. Save and exit (Ctrl+O, Enter, Ctrl+X).

---

## 4. Install Docker on EC2

SSH into the instance, then:

```bash
sudo apt-get update && sudo apt-get install -y docker.io
sudo usermod -aG docker ubuntu
```

Log out and log back in so the `docker` group membership applies (or run `newgrp docker` in the same session). Verify:

```bash
docker run hello-world
```

If you need to run Docker without re-login (e.g. in a script), use `sudo docker` for the commands below.

---

## 5. Build and run the agent container

On the server:

```bash
cd ~/livekit-agent
sudo docker build -t livekit-agent .
sudo docker run --env-file .env -d --restart unless-stopped --name agent livekit-agent
```

- `--env-file .env` passes all variables from `~/livekit-agent/.env` into the container (including `LIVEKIT_*` and provider API keys).
- `--restart unless-stopped` restarts the container after reboot or crash.

You should see a long container ID; the agent is running.

---

## 6. Verify the agent

### 6.1 Container status

```bash
sudo docker ps
```

The `agent` container should be listed with status **Up**.

### 6.2 Logs

```bash
sudo docker logs -f agent
```

Look for a successful connection to LiveKit (e.g. worker registered or similar). Press Ctrl+C to stop following.

### 6.3 Use the agent

- **LiveKit Cloud Playground:** Open your project → Playground → join a room. The agent should join if dispatch is enabled.
- **Outbound calls:** Use the same LiveKit project from your app or the `make_outbound_call.py` script (from your PC or from EC2; see below).

---

## 7. Trigger outbound calls from EC2

To run `make_outbound_call.py` on the EC2 instance (same flow as locally):

### 7.1 One-time: Python environment on the server

Using a venv and pip:

```bash
cd ~/livekit-agent
sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Or, if the project uses **uv**:

```bash
cd ~/livekit-agent
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv sync
```

### 7.2 Run the outbound call script

From `~/livekit-agent`, with `.env` present and venv activated (if used):

```bash
cd ~/livekit-agent
source .venv/bin/activate   # if using venv
python make_outbound_call.py +919500664509 ST_FKiPUcLVCjnp
```

Replace:

- `+919500664509` with the destination number in E.164 format (e.g. `+1...`).
- `ST_FKiPUcLVCjnp` with your **SIP trunk ID** from LiveKit Cloud (SIP → Trunks).

The script creates a room, dispatches your agent (the one in the Docker container), and connects the call via SIP. You can also keep triggering outbound calls from your local machine using the same script and the same `.env`; the agent on EC2 will handle them.

---

## 8. Updating the deployment

After code or config changes:

1. **Upload updated files** from your machine (e.g. `scp -r *` and optionally `.env` if you changed it).
2. **Rebuild and restart the container:**

```bash
cd ~/livekit-agent
sudo docker stop agent && sudo docker rm agent
sudo docker build -t livekit-agent .
sudo docker run --env-file .env -d --restart unless-stopped --name agent livekit-agent
```

---

## 9. Troubleshooting

| Issue | What to check |
|-------|----------------|
| `Permission denied (publickey)` | PEM path correct; PEM permissions (Windows: only your user has Read). |
| `open .env: no such file or directory` | `.env` was not uploaded; copy it with `scp` to `~/livekit-agent/`. |
| `invalid env file: variable 'X' contains whitespaces` | In `.env`, remove spaces around `=` and fix or remove duplicate keys. |
| Agent not joining rooms | `sudo docker logs agent`; confirm `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` and that the container is running. |
| Outbound call fails from EC2 | Same `.env` as agent; correct SIP trunk ID; Python deps installed; run from `~/livekit-agent`. |

---

## 10. Reference

- **LiveKit Cloud:** [cloud.livekit.io](https://cloud.livekit.io)
- **Agent deployment (overview):** [docs.livekit.io/deploy/agents/](https://docs.livekit.io/deploy/agents/)
- **Cloud deploy (alternative):** See [DEPLOY.md](./DEPLOY.md) for `lk agent deploy` and LiveKit Cloud–only workflow.

This project’s agent works with any LiveKit server (Cloud or self-hosted). This guide uses **LiveKit Cloud as the server** and **EC2 only for the agent worker**.
