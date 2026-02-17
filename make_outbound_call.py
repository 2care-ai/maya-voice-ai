"""
Helper script to trigger outbound calls using LiveKit Agent Dispatch.

Usage:
    python make_outbound_call.py +919500664509 ST_FKiPUcLVCjnp

Requirements:
    - Agent must be deployed to LiveKit Cloud
    - SIP trunk must be configured in LiveKit dashboard
    - Environment variables must be set in .env file
"""

import asyncio
import sys
import os
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from livekit import api

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")


def _env(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key) or default
    return v.strip() if isinstance(v, str) else v


async def _start_room_composite_egress(room_name: str, *, audio_only: bool = True) -> bool:
    bucket = _env("S3_BUCKET_NAME")
    access_key = _env("AWS_ACCESS_KEY_ID")
    secret = _env("AWS_SECRET_ACCESS_KEY")
    region = _env("AWS_REGION")
    if not all((bucket, access_key, secret, region)):
        print("   ⚠ Egress skipped: set S3_* and AWS_* env vars for call recording")
        return False
    livekit_url = _env("LIVEKIT_URL")
    api_key = _env("LIVEKIT_API_KEY")
    api_secret = _env("LIVEKIT_API_SECRET")
    if not all((livekit_url, api_key, api_secret)):
        print("   ⚠ Egress skipped: LIVEKIT_* env vars required")
        return False
    http_url = livekit_url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    prefix = (_env("S3_RECORDINGS_FOLDER") or "recordings/").rstrip("/") + "/"
    filepath = f"{prefix}{{room_name}}-{{time}}.mp4"
    s3 = api.S3Upload(
        access_key=access_key,
        secret=secret,
        region=region,
        bucket=bucket,
        endpoint=_env("AWS_ENDPOINT") or "",
        force_path_style=(_env("AWS_FORCE_PATH_STYLE") or "").lower() in ("1", "true", "yes"),
    )
    file_output = api.EncodedFileOutput(
        file_type=api.EncodedFileType.MP4,
        filepath=filepath,
        s3=s3,
    )
    req = api.RoomCompositeEgressRequest(
        room_name=room_name,
        audio_only=audio_only,
        layout="single-speaker",
        file_outputs=[file_output],
    )
    egress_api = api.LiveKitAPI(url=http_url, api_key=api_key, api_secret=api_secret)
    try:
        info = await egress_api.egress.start_room_composite_egress(req)
        print(f"   ✅ Call recording started (egress_id={info.egress_id})")
        return True
    except Exception as e:
        print(f"   ⚠ Recording start failed: {e}")
        return False
    finally:
        await egress_api.aclose()


async def make_outbound_call(
    phone_number: str,
    sip_trunk_id: str,
    room_name: str | None = None,
    patient_name: str | None = None,
):
    """
    Initiate an outbound call via LiveKit SIP.

    Args:
        phone_number: Phone number to call (E.164 format, e.g., +919500664509)
        sip_trunk_id: SIP trunk ID to use for the call
        room_name: Optional room name (auto-generated if not provided)
        patient_name: Optional patient name for the agent (passed to agent; no default)
    """
    # Input validation
    if not phone_number or not sip_trunk_id:
        print("❌ Error: phone_number and sip_trunk_id are required")
        return

    # Create LiveKit API client
    lkapi = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET")
    )

    if not room_name:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        room_name = f"outbound-call-{timestamp}"

    # Prepare metadata (agent reads phone_number, patient_name from this)
    meta = {
        "phone_number": phone_number,
        "sip_trunk_id": sip_trunk_id,
        "call_type": "outbound",
        "initiated_at": datetime.now().isoformat(),
    }
    if patient_name:
        meta["patient_name"] = patient_name
    metadata = json.dumps(meta)
    
    print(f"\n📞 Initiating outbound call...")
    print(f"   Phone: {phone_number}")
    print(f"   Trunk: {sip_trunk_id}")
    print(f"   Room: {room_name}")
    print(f"   Patient name: {patient_name or '(not set)'}")
    print(f"   Agent: CA_3cRGBuHyaPh4")
    
    try:
        # 1. Create the room
        await lkapi.room.create_room(
            api.CreateRoomRequest(
                name=room_name,
                metadata=metadata
            )
        )
        print(f"✅ Room created: {room_name}")

        # 2. Start room composite egress (call recording to S3)
        await _start_room_composite_egress(room_name, audio_only=True)

        # 3. Start the SIP Participant (this makes the actual phone call)
        print(f"📞 Calling {phone_number}...")
        await lkapi.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=sip_trunk_id,
                sip_call_to=phone_number,
                room_name=room_name,
                participant_identity=f"sip_{phone_number.replace('+', '')}",
                participant_name="Maya - Everhope Oncology",
                wait_until_answered=True
            )
        )
        print(f"✅ SIP participant connected!")

        # 4. Monitoring Information
        print(f"\n✅ Trigger complete!")
        print(f"   Room: {room_name}")
        
        # Construct the dashboard URL for monitoring
        # Extract project name from URL if possible (fallback to credira)
        project_name = "credira"
        lk_url = os.getenv("LIVEKIT_URL", "")
        if "livekit.cloud" in lk_url:
            parts = lk_url.replace("https://", "").split(".")
            if parts:
                project_name = parts[0].split("-")[0]
        
        dashboard_url = f"https://cloud.livekit.io/projects/{project_name}/rooms/{room_name}"
        
        print(f"\n🎯 The agent will now:")
        print(f"   1. Join the room '{room_name}'")
        print(f"   2. Initiate an outbound call to {phone_number}")
        print(f"   3. Wait for the callee to answer")
        print(f"   4. Start the conversation as Maya")
        print(f"\n💡 Monitor the call in LiveKit Dashboard:")
        print(f"   {dashboard_url}")
        
    except Exception as e:
        print(f"\n❌ Error initiating outbound call: {e}")
        print(f"\nTroubleshooting:")
        print(f"   1. Ensure agent 'CA_3cRGBuHyaPh4' is deployed to LiveKit Cloud")
        print(f"   2. Verify SIP trunk '{sip_trunk_id}' exists in dashboard")
        print(f"   3. Check LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET in .env")
        raise
    finally:
        # Properly close the API client session to avoid warnings
        await lkapi.aclose()


async def main():
    if len(sys.argv) < 3:
        print("Usage: python make_outbound_call.py <phone_number> <sip_trunk_id> [room_name] [patient_name]")
        print("\nExamples:")
        print("  python make_outbound_call.py +919500664509 ST_FKiPUcLVCjnp")
        print("  python make_outbound_call.py +919500664509 ST_FKiPUcLVCjnp my-room Raj")
        print("\nRequired:")
        print("  phone_number   - Phone number to call (E.164, e.g., +919876543210)")
        print("  sip_trunk_id   - SIP trunk ID from LiveKit dashboard (starts with ST_)")
        print("\nOptional:")
        print("  room_name      - Custom room name (auto-generated if not provided)")
        print("  patient_name  - Patient name for Maya (optional)")
        sys.exit(1)

    phone_number = sys.argv[1]
    sip_trunk_id = sys.argv[2]
    room_name = sys.argv[3] if len(sys.argv) > 3 else None
    patient_name = sys.argv[4] if len(sys.argv) > 4 else None

    await make_outbound_call(phone_number, sip_trunk_id, room_name, patient_name)


if __name__ == "__main__":
    asyncio.run(main())