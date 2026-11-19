"""Built-in catalog: fact schema, record kinds, choice sets and the question bank."""

import re
from functools import lru_cache

from .choices import CHOICE_SETS, DEGREE_RANK
from .facts import FACTS
from .playbooks import CHECKLIST, PLAYBOOKS, STYLE, playbook_for
from .questions import QUESTIONS
from .records import RECORD_KINDS, STORY_THEMES

__all__ = ["CHECKLIST", "CHOICE_SETS", "DEGREE_RANK", "FACTS", "PLAYBOOKS", "QUESTIONS", "RECORD_KINDS",
           "STORY_THEMES", "STYLE", "choice_set", "playbook_for"]


@lru_cache(maxsize=None)
def choice_set(name: str) -> dict:
    """Choice set with synonyms normalized and ignore patterns compiled."""
    from ..text import normalize

    raw = CHOICE_SETS[name]
    return {
        "_values": {canon: sorted({normalize(s) for s in syns}, key=len, reverse=True)
                    for canon, syns in raw["values"].items()},
        "_ignore": [re.compile(p) for p in raw.get("ignore", ())],
        "_labels": raw.get("labels", {}),
        "_parents": raw.get("parents", {}),
    }
