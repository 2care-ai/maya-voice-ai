import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from livekit import api

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

# ⚠️ IMPORTANT: Set your LiveKit SIP Trunk ID here
# Get this from LiveKit Dashboard → SIP → Trunks (should start with TR_ for outbound)
LIVEKIT_SIP_TRUNK_ID = "ST_FKiPUcLVCjnp"  # ← UPDATE THIS!


async def make_outbound_call(phone_number: str, room_name: str = None):
    """
    Make an outbound call to a phone number via Twilio SIP trunk
    
    Args:
        phone_number: Phone number in E.164 format (e.g., "+14155551234")
        room_name: Optional room name. If not provided, auto-generated.
    
    Returns:
        dict: Call information including room name and participant ID
    """
    # Initialize LiveKit API
    livekit_api = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )
    
    # Generate room name if not provided
    if not room_name:
        from datetime import datetime
        room_name = f"outbound-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    
    # Your Twilio SIP trunk ID from LiveKit
    trunk_id = LIVEKIT_SIP_TRUNK_ID
    
    print(f"📞 Initiating outbound call...")
    print(f"   Phone: {phone_number}")
    print(f"   Room: {room_name}")
    print(f"   Trunk: {trunk_id}")
    
    try:
        # Create SIP participant (initiate the call)
        result = await livekit_api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
                room_name=room_name,
                participant_identity=f"caller-{phone_number.replace('+', '')}",
                participant_name=f"Phone {phone_number}",
            )
        )
        
        print(f"✅ Call initiated successfully!")
        print(f"   Participant ID: {result.participant_id}")
        print(f"   SIP Call ID: {result.sip_call_id}")
        print(f"\n🎯 Your agent should now join room: {room_name}")
        print(f"   Make sure your agent is running to handle the call!")
        
        return {
            "room_name": room_name,
            "participant_id": result.participant_id,
            "sip_call_id": result.sip_call_id,
            "phone_number": phone_number,
        }
        
    except Exception as e:
        print(f"❌ Error making call: {e}")
        raise


async def main():
    """
    Main function - edit the phone number below to test
    """
    # ⚠️ IMPORTANT: Replace with the phone number you want to call
    # Must be in E.164 format: +[country code][number]
    # Examples:
    #   US: "+14155551234"
    #   India: "+919876543210"
    
    phone_number = "+919500664509"  # ← CHANGE THIS!
    
    if phone_number == "+1234567890":
        print("⚠️  Please edit make_call.py and set a real phone number!")
        print("   Format: +[country code][number]")
        print("   Example: +14155551234 (US) or +919876543210 (India)")
        return
    
    # Make the call
    call_info = await make_outbound_call(phone_number)
    
    print("\n" + "="*60)
    print("Call Details:")
    print(f"  Room: {call_info['room_name']}")
    print(f"  Phone: {call_info['phone_number']}")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
