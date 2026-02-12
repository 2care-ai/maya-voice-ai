"""
Quick S3 connectivity check: upload a small test file using env keys.
Run: uv run python check_s3_upload.py
"""
import os
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

BUCKET = os.getenv("S3_BUCKET_NAME", "").strip()
PREFIX = (os.getenv("S3_RECORDINGS_FOLDER") or "recordings/").strip().rstrip("/") + "/"
KEY = f"{PREFIX}s3-connection-test.txt"
BODY = f"S3 connection test from livekit-101 at {datetime.now(timezone.utc).isoformat()}\n"

def main():
    if not BUCKET:
        print("Missing S3_BUCKET_NAME in .env")
        return 1
    try:
        import boto3
    except ImportError:
        print("Install boto3: uv add boto3")
        return 1
    client = boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )
    try:
        client.put_object(Bucket=BUCKET, Key=KEY, Body=BODY, ContentType="text/plain")
        print(f"OK: Uploaded to s3://{BUCKET}/{KEY}")
        return 0
    except Exception as e:
        print(f"FAIL: {e}")
        if "SignatureDoesNotMatch" in str(e):
            print("Tip: Check AWS_SECRET_ACCESS_KEY and that AWS_REGION matches the bucket region.")
        return 1

if __name__ == "__main__":
    exit(main())
