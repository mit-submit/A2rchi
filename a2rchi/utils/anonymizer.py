"""
Authors: Pietro Lugato, Hasan Ozturk
"""

import re
import spacy
from typing import Set, List


class Anonymizer:
    def __init__(self, excluded_words: Set[str] = None):
        """
        Initialize the Anonymizer.
        """
        try:
            self.nlp = spacy.load("en_core_web_sm")
        except OSError:
            spacy.cli.download("en_core_web_sm")
            self.nlp = spacy.load("en_core_web_sm")
        # TODO: Move the following patters into the config file
        self.EXCLUDED_WORDS = excluded_words or {"John", "Jane", "Doe"}
        self.GREETING_PATTERNS = [
            re.compile(r"^(hi|hello|hey|greetings|dear)\b", re.IGNORECASE),
            re.compile(r"^(\w+,\s*|\w+\s+)", re.IGNORECASE),
        ]
        self.SIGNOFF_PATTERNS = [
            re.compile(r"\b(regards|sincerely|best regards|cheers|thank you)\b", re.IGNORECASE),
            re.compile(r"^\s*[-~]+\s*$"),
        ]
        self.email_pattern = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
        # TODO: This is JIRA specific, consider making it configurable
        self.username_pattern = re.compile(r'\[~[^\]]+\]')

    def anonymize(self, text: str) -> str:
        """
        Anonymize names, emails, usernames, greetings, and sign-offs from the text.
        """
        doc = self.nlp(text)
        names_to_replace = {
            ent.text for ent in doc.ents
            if ent.label_ == "PERSON" and ent.text not in self.EXCLUDED_WORDS
        }

        # Remove email addresses and usernames
        text = self.email_pattern.sub("", text)
        text = self.username_pattern.sub("", text)

        # Remove greetings and sign-offs
        lines = text.splitlines()
        filtered_lines: List[str] = []
        for line in lines:
            stripped_line = line.strip()
            if any(p.match(stripped_line) for p in self.GREETING_PATTERNS):
                continue
            if any(p.match(stripped_line) for p in self.SIGNOFF_PATTERNS):
                continue
            filtered_lines.append(line)
        text = "\n".join(filtered_lines)

        # Remove names (case-insensitive)
        for name in sorted(names_to_replace, key=len, reverse=True):
            pattern = re.compile(r'\b' + re.escape(name) + r'\b', re.IGNORECASE)
            text = pattern.sub("", text)

        # Remove extra whitespace
        text = "\n".join(line for line in text.splitlines() if line.strip())

        return text