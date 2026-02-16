"""
Outbound calls via LiveKit using the Plivo (Indian number) SIP trunk.

Usage:
    python make_outbound_call_plivo.py +919500664509
    python make_outbound_call_plivo.py +919500664509 Kiran           # agent opens with "May I speak with Kiran?"
    python make_outbound_call_plivo.py +919500664509 Kiran my-room   # name then room (room optional)

Set LIVEKIT_SIP_TRUNK_ID_PLIVO in .env to your Plivo trunk ID from the LiveKit dashboard.
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
    patient_name: str | None = None,
    room_name: str | None = None,
):
    """
    Initiate an outbound call via LiveKit SIP using the Plivo trunk.

    Args:
        phone_number: E.164 number (e.g. +919500664509)
        sip_trunk_id: Plivo SIP trunk ID from LiveKit dashboard
        patient_name: Optional; for diagnostic-lab leads, used in opening ("May I speak with [name]?")
        room_name: Optional; auto-generated if not provided (usually omitted)
    """
    if not phone_number or not sip_trunk_id:
        print("❌ Error: phone_number and sip_trunk_id are required")
        return

    lkapi = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )

    if not room_name:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        room_name = f"outbound-plivo-{timestamp}"

    meta = {
        "phone_number": phone_number,
        "sip_trunk_id": sip_trunk_id,
        "call_type": "outbound",
        "trunk": "plivo",
        "initiated_at": datetime.now().isoformat(),
    }
    if patient_name:
        meta["patient_name"] = patient_name.strip()
        meta["lead_source"] = "diagnostic_lab"
    metadata = json.dumps(meta)

    AGENT_NAME = "maya-agent"

    print(f"\n📞 Initiating outbound call (Plivo / Indian trunk)...")
    print(f"   Phone: {phone_number}")
    if patient_name:
        print(f"   Patient: {patient_name}")
    print(f"   Trunk: {sip_trunk_id}")
    print(f"   Room: {room_name}")
    print(f"   Agent: {AGENT_NAME}")

    try:
        await lkapi.room.create_room(
            api.CreateRoomRequest(name=room_name, metadata=metadata)
        )
        print(f"✅ Room created: {room_name}")

        dispatch = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata,
            )
        )
        print(f"✅ Agent dispatch created (dispatch_id={dispatch.id})")

        await _start_room_composite_egress(room_name, audio_only=True)

        print(f"📞 Calling {phone_number}...")
        await lkapi.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=sip_trunk_id,
                sip_call_to=phone_number,
                room_name=room_name,
                participant_identity=f"sip_{phone_number.replace('+', '')}",
                participant_name="Maya - Everhope Oncology",
                wait_until_answered=True,
            )
        )
        print(f"✅ SIP participant connected!")

        print(f"\n✅ Trigger complete!")
        print(f"   Room: {room_name}")

        project_name = "credira"
        lk_url = os.getenv("LIVEKIT_URL", "")
        if "livekit.cloud" in lk_url:
            parts = lk_url.replace("https://", "").split(".")
            if parts:
                project_name = parts[0].split("-")[0]

        dashboard_url = f"https://cloud.livekit.io/projects/{project_name}/rooms/{room_name}"

        print(f"\n🎯 Flow:")
        print(f"   1. Agent '{AGENT_NAME}' is dispatched to room '{room_name}'")
        print(f"   2. Plivo dials {phone_number}; on answer, caller joins the room")
        print(f"   3. Maya speaks and runs the conversation in the room")
        print(f"\n💡 Monitor: {dashboard_url}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        print(f"\nTroubleshooting:")
        print(f"   1. Ensure agent '{AGENT_NAME}' is deployed and registered with LiveKit Cloud")
        print(f"   2. Verify Plivo SIP trunk '{sip_trunk_id}' exists in LiveKit dashboard")
        print(f"   3. Check LIVEKIT_* and LIVEKIT_SIP_TRUNK_ID_PLIVO in .env")
        raise
    finally:
        await lkapi.aclose()


async def main():
    if len(sys.argv) < 2:
        print("Usage: python make_outbound_call_plivo.py <phone_number> [patient_name] [room_name]")
        print("\nExample:")
        print("  python make_outbound_call_plivo.py +919500664509")
        print("  python make_outbound_call_plivo.py +919500664509 Kiran")
        print("  python make_outbound_call_plivo.py +919500664509 Kiran my-room")
        print("\nRequired:")
        print("  phone_number  - E.164 format (e.g. +919876543210)")
        print("\nOptional:")
        print("  patient_name  - For diagnostic-lab leads; agent opens with 'May I speak with [name]?'")
        print("  room_name     - Custom room name (default: outbound-plivo-<timestamp>; usually omitted)")
        print("\nEnv:")
        print("  LIVEKIT_SIP_TRUNK_ID_PLIVO - Plivo SIP trunk ID from LiveKit dashboard")
        sys.exit(1)

    phone_number = sys.argv[1]
    patient_name = sys.argv[2] if len(sys.argv) > 2 else None
    room_name = sys.argv[3] if len(sys.argv) > 3 else None

    sip_trunk_id = _env("LIVEKIT_SIP_TRUNK_ID_PLIVO")
    if not sip_trunk_id:
        print("❌ Set LIVEKIT_SIP_TRUNK_ID_PLIVO in .env to your Plivo trunk ID (LiveKit dashboard → SIP → your Plivo trunk).")
        sys.exit(1)

    await make_outbound_call(phone_number, sip_trunk_id, patient_name, room_name)


if __name__ == "__main__":
    asyncio.run(main())
