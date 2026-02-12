from enum import Enum
import re


class FlowState(str, Enum):
    RECORDING_INTRO = "recording_intro"
    DISCOVERY_WHO = "discovery_who"
    DISCOVERY_CANCER = "discovery_cancer"
    DISCOVERY_CITY = "discovery_city"
    POSITIONING = "positioning"
    DR_GARG = "dr_garg"
    CLOSING = "closing"
    DONE = "done"


STEP_INSTRUCTIONS = {
    FlowState.RECORDING_INTRO: (
        "First message only—keep it very short. Say: Hello, I'm Maya from Everhope Oncology, calling regarding your cancer form submission? Is this a good time to talk?."
    ),
    FlowState.DISCOVERY_WHO: (
        "Ask whether treatment is for them or a family member. Single question. If they ask something else, answer that first then ask this when natural."
    ),
    FlowState.DISCOVERY_CANCER: (
        "Ask what type of cancer and stage if they know. Single question. If they share, acknowledge briefly then ask. If they ask something else, answer first then return to this."
    ),
    FlowState.DISCOVERY_CITY: (
        "Ask which city they want treatment in. Single question. If they ask something else, answer first then ask about city when natural."
    ),
    FlowState.POSITIONING: (
        "Briefly say Everhope does personalized plans and advanced care, not one-size-fits-all. If they ask something else, answer first then say this when it fits."
    ),
    FlowState.DR_GARG: (
        "Mention Dr. Sunny Garg, lead oncologist, has helped many like them. Offer to set up a consultation. If they say yes: team will reach out, virtual or in-person. If they ask something else (e.g. cost), answer first then offer consultation."
    ),
    FlowState.CLOSING: (
        "Thank them, say you look forward to helping, wish them a hopeful day. If they ask one more thing, answer briefly then close."
    ),
    FlowState.DONE: (
        "Flow complete. Respond warmly and briefly to anything they say."
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


def _looks_like_objection(text: str) -> bool:
    t = _normalize(text)
    objection = ("think", "busy", "time", "not sure", "uncertain", "another doctor", "already", "later", "call back", "soche", "soch", "time nahi", "abhi nahi")
    return any(o in t for o in objection)


def _looks_like_answer_who(text: str) -> bool:
    t = _normalize(text)
    return any(x in t for x in ("myself", "self", "me", "family", "father", "mother", "parent", "wife", "husband", "relative", "apne liye", "family ke", "kisi aur"))


def _looks_like_answer_cancer(text: str) -> bool:
    t = _normalize(text)
    if len(t) < 4:
        return False
    return "cancer" in t or "stage" in t or "tumor" in t or "detect" in t or "lung" in t or "breast" in t or "blood" in t or "type" in t or any(d in t for d in ("1", "2", "3", "4", "one", "two", "three", "four", "i", "ii", "iii", "iv"))


def _looks_like_answer_city(text: str) -> bool:
    t = _normalize(text)
    if len(t) < 2:
        return False
    city_like = re.search(r"\b([a-z]{2,20})\b", t)
    if not city_like:
        return False
    stopwords = ("the", "for", "and", "yes", "no", "ok", "okay", "sure", "alright", "think", "maybe", "please", "help", "want", "looking", "treatment", "city", "mein", "ke", "liye", "dekh", "rah")
    words = [w for w in t.split() if w not in stopwords and len(w) > 1]
    return len(words) >= 1 and not _looks_like_question(t)


def _looks_like_yes_consultation(text: str) -> bool:
    t = _normalize(text)
    return any(x in t for x in ("yes", "sure", "ok", "okay", "schedule", "book", "confirm", "haan", "ji haan", "theek", "karo", "kar do", "kar sakte", "please"))


def get_step_instruction(state: FlowState) -> str:
    return STEP_INSTRUCTIONS.get(state, STEP_INSTRUCTIONS[FlowState.DONE])


def get_next_state(current: FlowState, user_message: str) -> FlowState:
    msg = _normalize(user_message or "")
    if not msg and current != FlowState.DONE:
        return current

    if current == FlowState.RECORDING_INTRO:
        return FlowState.DISCOVERY_WHO

    if current == FlowState.DISCOVERY_WHO:
        if _looks_like_question(msg) or _looks_like_objection(msg):
            return current
        if _looks_like_answer_who(msg):
            return FlowState.DISCOVERY_CANCER
        return current

    if current == FlowState.DISCOVERY_CANCER:
        if _looks_like_question(msg) or _looks_like_objection(msg):
            return current
        if _looks_like_answer_cancer(msg) or len(msg) > 15:
            return FlowState.DISCOVERY_CITY
        return current

    if current == FlowState.DISCOVERY_CITY:
        if _looks_like_question(msg) or _looks_like_objection(msg):
            return current
        if _looks_like_answer_city(msg) or len(msg) > 3:
            return FlowState.POSITIONING
        return current

    if current == FlowState.POSITIONING:
        if _looks_like_question(msg) or _looks_like_objection(msg):
            return current
        return FlowState.DR_GARG

    if current == FlowState.DR_GARG:
        if _looks_like_yes_consultation(msg):
            return FlowState.CLOSING
        if _looks_like_objection(msg):
            return current
        return FlowState.DR_GARG

    if current == FlowState.CLOSING:
        return FlowState.DONE

    return FlowState.DONE
