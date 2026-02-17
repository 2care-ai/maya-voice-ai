from enum import Enum
import re


class FlowState(str, Enum):
    RECORDING_INTRO = "recording_intro"
    CALLBACK_REQUESTED = "callback_requested"
    TYPE_AND_STAGE = "type_and_stage"
    BIOPSY_DONE = "biopsy_done"
    TREATMENT_STATUS = "treatment_status"
    CITY_AND_TRAVEL = "city_and_travel"
    POSITIONING = "positioning"
    DR_GARG = "dr_garg"
    CLOSING = "closing"
    DONE = "done"


STEP_INSTRUCTIONS = {
    FlowState.RECORDING_INTRO: (
        "First message only—must be in Hinglish. Keep it very short. Say:\n"
        "<hinglish/> Hello, main Maya bol rahi hoon Everhope Oncology se, aapke recent medical test ke baare mein. Kya ab baat karne ka time theek hai?"
    ),
    FlowState.CALLBACK_REQUESTED: (
        "User said busy or call back later. Ask for a comfortable time to callback in Hinglish. Do NOT continue the flow. Say:\n"
        "<hinglish/> Theek hai, koi baat nahi. Kab call karein aapko? Koi convenient time bataiye, hum usi time pe call karenge."
    ),
    FlowState.TYPE_AND_STAGE: (
        "Ask ONE question. If they share type or stage, acknowledge with empathy first (e.g. I understand; that must be difficult), then ask. If they ask something else, answer first then return to this.\n"
        "<english/> To help me understand better, what type of cancer was detected, and do you know the current stage?\n"
        "<hinglish/> Better samajhne ke liye, kis type ka cancer detect hua hai aur stage kya hai agar pata ho?"
    ),
    FlowState.BIOPSY_DONE: (
        "Ask ONE question. Acknowledge their answer (e.g. Got it, I understand). If they ask something else, answer first then ask.\n"
        "<english/> Has a biopsy been done, or is that still pending?\n"
        "<hinglish/> Biopsy ho chuki hai ya abhi pending hai?"
    ),
    FlowState.TREATMENT_STATUS: (
        "Ask about treatment status. If they say treatment has already started, ask which hospital. If not started, ask when they are planning to start. One question at a time. Acknowledge before asking the follow-up.\n"
        "<english/> Has your treatment already started? If yes, which hospital are you at? If not, when are you planning to start your treatment?\n"
        "<hinglish/> Kya treatment shuru ho chuka hai? Agar haan, toh kaunse hospital mein? Agar nahi, toh kab start karne ki soch rahe hain?"
    ),
    FlowState.CITY_AND_TRAVEL: (
        "Ask TWO things in a natural way: which city they are from, and whether they would be willing to travel. If they ask something else, answer first then ask. Acknowledge their answer.\n"
        "<english/> Which city are you from? And would you be willing to travel for treatment if needed?\n"
        "<hinglish/> Aap kis city se hain? Aur kya aap treatment ke liye travel kar sakte hain agar zarurat ho?"
    ),
    FlowState.POSITIONING: (
        "Briefly give the Everhope pitch. If they ask something else, answer first then say when it fits.\n"
        "<english/> At Everhope, we don't believe in one-size-fits-all. Every patient gets a treatment plan designed specifically for them, with advanced technology and personalized care.\n"
        "<hinglish/> Everhope mein har patient ke liye personalized plan hota hai, advanced care; sab pe same treatment nahi chalta."
    ),
    FlowState.DR_GARG: (
        "Mention Dr. Sunny Garg and offer consultation. If yes: team will reach out, virtual or in-person. If they ask something else (e.g. cost), answer first then offer consultation.\n"
        "<english/> Our lead oncologist, Dr. Sunny Garg, has helped many patients in situations like yours. Would you be interested in a consultation? He can give you a clear picture of the best options.\n"
        "<hinglish/> Hamaare lead oncologist Dr. Sunny Garg ne kaafi patients ki help ki hai aapki jaisi situation mein. Kya main unse consultation fix karwa doon? Unse baat karke aapko clarity mil jayegi."
    ),
    FlowState.CLOSING: (
        "Thank them, say you look forward to helping, wish them a hopeful day. If they ask one more thing, answer briefly then close.\n"
        "<english/> Thank you for speaking with me today. We look forward to helping you. Have a hopeful day ahead.\n"
        "<hinglish/> Thank you baat karne ke liye. Hum poori koshish karenge aapki help karne ki. Have a hopeful day ahead!"
    ),
    FlowState.DONE: (
        "Flow complete. Respond warmly and briefly in the user's language to anything they say.\n"
        "<english/> Respond in English.\n"
        "<hinglish/> Respond in Hinglish."
    ),
}


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def _looks_like_question(text: str) -> bool:
    t = _normalize(text)
    if len(t) < 3:
        return False
    question_start = ("how", "what", "when", "where", "who", "why", "which", "is", "are", "do", "does", "can", "could", "will", "would", "kya", "kaise", "kitna", "kab", "kahan", "kon")
    first = t.split()[0] if t.split() else ""
    if first in question_start or t.endswith("?"):
        return True
    return "?" in t or " cost" in t or " price" in t or " fee" in t or " charge" in t


def _looks_like_clarification_question(text: str) -> bool:
    """User asking who you are, where you're calling from, what this call is—do not advance flow."""
    t = _normalize(text)
    if len(t) < 4:
        return False
    clarification = (
        "who are you", "who is this", "where are you calling", "where you calling from",
        "what is this call", "what is this about", "which company", "which organization",
        "kaun bol raha", "kaun hai", "kahan se call", "kis liye call", "kya call hai",
        "kon bol raha", "kis company se", "ye call kyon",
    )
    return any(c in t for c in clarification)


def _looks_like_objection(text: str) -> bool:
    t = _normalize(text)
    objection = ("think", "busy", "time", "not sure", "uncertain", "another doctor", "already", "later", "call back", "soche", "soch", "time nahi", "abhi nahi")
    return any(o in t for o in objection)


def _looks_like_busy_or_callback(text: str) -> bool:
    """User said busy, call back later, no time, etc. — ask for callback time and end."""
    t = _normalize(text)
    if len(t) < 2:
        return False
    return any(x in t for x in (
        "busy", "call back", "callback", "later", "not now", "no time", "abhi nahi",
        "baad mein", "time nahi", "busy hoon", "call back karo", "baad mein bolo"
    )) or (len(t) > 4 and ("call" in t and "back" in t or "kab" in t and "baad" in t))


def _looks_like_answer_good_time(text: str) -> bool:
    """Any substantive reply to 'is this a good time?' — yes, no, sure, etc. (excludes busy/callback)."""
    if _looks_like_busy_or_callback(text):
        return False
    t = _normalize(text)
    if len(t) < 2:
        return False
    yes_like = ("yes", "sure", "ok", "okay", "fine", "go ahead", "haan", "ji haan", "theek", "bolo", "speak", "baat")
    no_like = ("no", "nahi")
    return any(x in t for x in yes_like) or any(x in t for x in no_like) or len(t) > 5



def _looks_like_answer_type_stage(text: str) -> bool:
    """They're sharing cancer type or stage."""
    t = _normalize(text)
    if len(t) < 3:
        return False
    return (
        "cancer" in t or "stage" in t or "tumor" in t or "detect" in t
        or "lung" in t or "breast" in t or "blood" in t or "type" in t
        or any(d in t for d in ("1", "2", "3", "4", "one", "two", "three", "four", "i", "ii", "iii", "iv"))
        or len(t) > 12
    )


def _looks_like_answer_biopsy(text: str) -> bool:
    """They're answering whether biopsy was done."""
    t = _normalize(text)
    if len(t) < 2:
        return False
    return any(x in t for x in (
        "yes", "no", "done", "pending", "not yet", "biopsy",
        "haan", "nahi", "ho chuki", "ho chuka", "abhi nahi", "pending"
    )) or len(t) > 6


def _looks_like_answer_treatment_status(text: str) -> bool:
    """They're answering treatment status: started + hospital, or not started + when."""
    t = _normalize(text)
    if len(t) < 3:
        return False
    started = ("started", "already", "hospital", "going to", "taking", "treatment", "therapy")
    not_started = ("not yet", "not started", "planning", "will start", "next", "month", "week", "abhi nahi", "plan")
    return any(x in t for x in started) or any(x in t for x in not_started) or len(t) > 10


def _looks_like_answer_city(text: str) -> bool:
    t = _normalize(text)
    if len(t) < 2:
        return False
    city_like = re.search(r"\b([a-z]{2,20})\b", t)
    if not city_like:
        return False
    stopwords = ("the", "for", "and", "yes", "no", "ok", "okay", "sure", "alright", "think", "maybe", "please", "help", "want", "looking", "treatment", "city", "mein", "ke", "liye", "dekh", "rah", "travel")
    words = [w for w in t.split() if w not in stopwords and len(w) > 1]
    return len(words) >= 1 and not _looks_like_question(t)


def _looks_like_answer_travel(text: str) -> bool:
    """They're indicating willingness (or not) to travel."""
    t = _normalize(text)
    if len(t) < 2:
        return False
    return any(x in t for x in (
        "yes", "no", "travel", "willing", "can", "sure", "nahi", "haan",
        "kar sakte", "sakta", "sakti", "willing", "theek"
    )) or len(t) > 5


def _looks_like_yes_consultation(text: str) -> bool:
    t = _normalize(text)
    return any(x in t for x in ("yes", "sure", "ok", "okay", "schedule", "book", "confirm", "haan", "ji haan", "theek", "karo", "kar do", "kar sakte", "please"))


def get_step_instruction(state: FlowState) -> str:
    return STEP_INSTRUCTIONS.get(state, STEP_INSTRUCTIONS[FlowState.DONE])


def get_next_state(current: FlowState, user_message: str) -> FlowState:
    msg = _normalize(user_message or "")
    if not msg and current != FlowState.DONE:
        return current
    if _looks_like_clarification_question(msg):
        return current

    if current == FlowState.RECORDING_INTRO:
        if _looks_like_busy_or_callback(msg):
            return FlowState.CALLBACK_REQUESTED
        if _looks_like_answer_good_time(msg):
            return FlowState.TYPE_AND_STAGE
        return current

    if current == FlowState.CALLBACK_REQUESTED:
        return FlowState.CLOSING

    if current == FlowState.TYPE_AND_STAGE:
        if _looks_like_busy_or_callback(msg):
            return FlowState.CALLBACK_REQUESTED
        if _looks_like_answer_type_stage(msg):
            return FlowState.BIOPSY_DONE
        if _looks_like_question(msg) or _looks_like_objection(msg):
            return current
        return current

    if current == FlowState.BIOPSY_DONE:
        if _looks_like_busy_or_callback(msg):
            return FlowState.CALLBACK_REQUESTED
        if _looks_like_answer_biopsy(msg):
            return FlowState.TREATMENT_STATUS
        if _looks_like_question(msg) or _looks_like_objection(msg):
            return current
        return current

    if current == FlowState.TREATMENT_STATUS:
        if _looks_like_busy_or_callback(msg):
            return FlowState.CALLBACK_REQUESTED
        if _looks_like_answer_treatment_status(msg):
            return FlowState.CITY_AND_TRAVEL
        if _looks_like_question(msg) or _looks_like_objection(msg):
            return current
        if len(msg) > 8:
            return FlowState.CITY_AND_TRAVEL
        return current

    if current == FlowState.CITY_AND_TRAVEL:
        if _looks_like_busy_or_callback(msg):
            return FlowState.CALLBACK_REQUESTED
        if _looks_like_answer_city(msg) or _looks_like_answer_travel(msg) or len(msg) > 4:
            return FlowState.POSITIONING
        if _looks_like_question(msg) or _looks_like_objection(msg):
            return current
        return current

    if current == FlowState.POSITIONING:
        if _looks_like_busy_or_callback(msg):
            return FlowState.CALLBACK_REQUESTED
        if _looks_like_question(msg) or _looks_like_objection(msg):
            return current
        return FlowState.DR_GARG

    if current == FlowState.DR_GARG:
        if _looks_like_busy_or_callback(msg):
            return FlowState.CALLBACK_REQUESTED
        if _looks_like_yes_consultation(msg):
            return FlowState.CLOSING
        if _looks_like_objection(msg):
            return current
        return FlowState.DR_GARG

    if current == FlowState.CLOSING:
        return FlowState.DONE

    return FlowState.DONE
