"""
Authors: Pietro Lugato, Hasan Ozturk
"""

import re
import spacy
from typing import Set, List

from a2rchi.utils.config_loader import load_config


class Anonymizer:
    def __init__(self):
        """
        Initialize the Anonymizer.
        """
        self.anonymizer_config = load_config()["utils"]["anonymizer"]
        nlp_model = self.anonymizer_config["nlp_model"]
        excluded_words = self.anonymizer_config["excluded_words"]
        greeting_patterns = self.anonymizer_config["greeting_patterns"]
        signoff_patterns = self.anonymizer_config["signoff_patterns"]
        email_pattern = self.anonymizer_config["email_pattern"]
        username_pattern = self.anonymizer_config["username_pattern"]

        try:
            self.nlp = spacy.load(nlp_model)
        except OSError:
            spacy.cli.download(nlp_model)
            self.nlp = spacy.load(nlp_model)

        self.EXCLUDED_WORDS = excluded_words
        self.GREETING_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in greeting_patterns]
        self.SIGNOFF_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in signoff_patterns]
        self.EMAIL_PATTERN = re.compile(email_pattern)
        self.USERNAME_PATTERN = re.compile(username_pattern)

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
        text = self.EMAIL_PATTERN.sub("", text)
        text = self.USERNAME_PATTERN.sub("", text)

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