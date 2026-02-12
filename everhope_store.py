import os
import redis

_url = os.getenv("REDIS_URL", "rediss://default:AdYfAAIncDIzNjllOGY3ZmE4NGU0MWJkOTAwOTU4ZWNkNzVmODY4OXAyNTQ4MTU@content-cat-54815.upstash.io:6379")
r = redis.Redis.from_url(_url)

EVERHOPE_KNOWLEDGE_KEY = "everhope:knowledge-base"

def get_everhope_knowledge_base() -> str | None:
    raw = r.get(EVERHOPE_KNOWLEDGE_KEY)
    if raw is None:
        return None
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
