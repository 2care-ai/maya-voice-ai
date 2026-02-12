import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from livekit import api

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")


async def check_trunk():
    """Check SIP trunk configuration"""
    livekit_api = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )
    
    trunk_id = "ST_FKiPUcLVCjnp"
    
    print(f"🔍 Checking trunk: {trunk_id}\n")
    
    try:
        # List all SIP trunks
        print("📋 Listing all SIP trunks...")
        trunks = await livekit_api.sip.list_sip_trunk()
        
        print(f"\nFound {len(trunks.items)} trunk(s):\n")
        
        for trunk in trunks.items:
            print(f"{'='*60}")
            print(f"Trunk ID: {trunk.sip_trunk_id}")
            print(f"Name: {trunk.name}")
            print(f"Kind: {trunk.kind}")  # Should be "trunk_outbound"
            print(f"Outbound Address: {trunk.outbound_address}")
            print(f"Outbound Username: {trunk.outbound_username}")
            print(f"Numbers: {trunk.outbound_numbers}")
            print(f"Metadata: {trunk.metadata}")
            print(f"{'='*60}\n")
            
            if trunk.sip_trunk_id == trunk_id:
                print(f"✅ Found your trunk!")
                if trunk.kind != "trunk_outbound":
                    print(f"⚠️  WARNING: This trunk is '{trunk.kind}', not 'trunk_outbound'")
                    print(f"   You need an OUTBOUND trunk to make calls!")
                
        if not any(t.sip_trunk_id == trunk_id for t in trunks.items):
            print(f"❌ Trunk {trunk_id} not found in your project!")
            print(f"   Make sure you're using the correct LiveKit project.")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(check_trunk())
