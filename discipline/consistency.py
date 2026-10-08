"""Keep an incident's parent-contact and follow-up fields in line with its notes.

The notes are free text, so this looks for plain statements in them ("spoke to
his mother", "a meeting is arranged for Friday") and reports when the
structured fields say otherwise. Statements about what still has to happen
("parents will be called", "could not reach the father") don't count as contact.
"""
import re

_SENTENCE = re.compile(r"[^.!?\n]+")

_PARENT = re.compile(r"\b(parents?|mother|mum|mom|father|dad|guardians?|family)\b", re.I)
_CONTACT = re.compile(
    r"\b(called|phoned|rang|telephoned|spoke|spoken|talked|informed|contacted|notified|"
    r"met|emailed|messaged|texted|wrote)\b"
    r"|\bmeeting\b.*\b(is|was|has been|have been|been)\s+(arranged|scheduled|set|booked|held|agreed)\b"
    r"|\b(arranged|scheduled|booked|held|had)\s+(a\s+)?meeting\b",
    re.I,
)
_NOT_YET = re.compile(
    r"\b(not|no|never|unable|couldn'?t|could not|wasn'?t|hasn'?t|haven'?t|didn'?t|yet|"
    r"should|needs? to|pending|try|tried|attempt(ed)?)\b"
    r"|\bwill\s+(be\s+)?(call|phone|ring|contact|inform|notify|speak|talk|email|message|write|meet)"
    r"|\bto be\s+(called|phoned|contacted|informed|notified)\b",
    re.I,
)

_FOLLOW_UP = re.compile(r"\b(meeting|appointment|follow[- ]?up|conference)\b", re.I)
_ARRANGED = re.compile(r"\b(arranged|scheduled|set|booked|planned|fixed|agreed|organi[sz]ed|will)\b", re.I)
_CANCELLED = re.compile(r"\b(no|not|cancel+ed|cancel)\b", re.I)


def _sentences(texts):
    for text in texts:
        for match in _SENTENCE.finditer(text or ""):
            sentence = match.group().strip()
            if sentence:
                yield sentence


def _quote(sentence):
    return f"“{sentence[:90]}{'…' if len(sentence) > 90 else ''}”"


def notes_conflicts(notes, parent_contacted, follow_up_arranged):
    """Plain-language errors where the notes contradict the fields; [] if they agree."""
    contact_said = arranged_said = None
    for sentence in _sentences(notes):
        if (contact_said is None and _PARENT.search(sentence) and _CONTACT.search(sentence)
                and not _NOT_YET.search(sentence)):
            contact_said = sentence
        if (arranged_said is None and _FOLLOW_UP.search(sentence) and _ARRANGED.search(sentence)
                and not _CANCELLED.search(sentence)):
            arranged_said = sentence

    errors = []
    if contact_said and not parent_contacted:
        errors.append(
            f"The notes record contact with the parent ({_quote(contact_said)}), but Parent contacted "
            "is No. Record the contact, or correct the notes."
        )
    if arranged_said and not follow_up_arranged:
        errors.append(
            f"The notes describe a follow-up ({_quote(arranged_said)}), but Follow-up is Not required. "
            "Record what is arranged, or correct the notes."
        )
    return errors
