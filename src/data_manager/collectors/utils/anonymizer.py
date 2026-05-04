"""
Authors: Pietro Lugato, Hasan Ozturk
"""

import re
from typing import List, Set, Dict, Any

import spacy

from src.utils.config_access import get_data_manager_config
from html import unescape

# Generic markup patterns
_TAG_RE = re.compile(r"<[^>]+>")
_CDATA_RE = re.compile(r"<!\[CDATA\[|\]\]>")
_DC_CREATOR_RE = re.compile(
    r'(<dc:creator><!\[CDATA\[)[^\]]*(\]\]></dc:creator>)',
    re.IGNORECASE,
)
_ATTR_TEXT_RE = re.compile(r'(?:title|alt|creator|author)=["\']([^"\']+)["\']', re.IGNORECASE)
_CONTENT_TAG_RE = re.compile(
    r'<(?:p|li|td|description|title|dc:creator)[^>]*>(.*?)</(?:p|li|td|description|title|dc:creator)>',
    re.DOTALL | re.IGNORECASE,
)
# <a href="/author/Albert-Einstein">Albert-Einstein</a> → (removed)
_DEFAULT_GENERIC_MARKUP_USER_LINK_RE = re.compile(
    r'<a[^>]*href="[^"]*?/(?:Main|author|user|profile|members)/[^"]*"[^>]*>[^<]*</a>',
    re.IGNORECASE,
)
# Generic author link, like <a href="/author/Albert-Einstein">Albert-Einstein</a>
# <small itemprop="author">Stephenie Meyer</small>
# <span class="author">John Doe</span>
# <a rel="author" href="...">Jane Smith</a>
# <div class="post-author meta">Bob</div>
_DEFAULT_GENERIC_MARKUP_AUTHOR_ELEMENT_RE = re.compile(
    r'<[^>]*(?:itemprop=["\']author["\']|class=["\'][^"\']*\bauthor\b[^"\']*["\']|rel=["\']author["\'])[^>]*>[^<]*</[^>]+>',
    re.IGNORECASE,
)
# <a class="twikiLink" href="/twiki/bin//Main/JohnDoe">JohnDoe</a> → (removed)
_DEFAULT_MARKUP_TWIKI_USER_LINK_RE = re.compile(
    r'<a[^>]*href="[^"]*?/twiki/bin/\w+/Main/\w+"[^>]*>\w+</a>',
    re.IGNORECASE,
)
# <p>John</p> → (removed)
# <p><br>John Doe</p> → (removed)
_DEFAULT_MARKUP_SIGNOFF_TAG_RE = re.compile(
    r'<p>\s*(?:<br\s*/?>)?\s*[A-Z][\w.]*(?:\s+[A-Z][\w.]*){0,2}\s*</p>',
    re.IGNORECASE,
)
# ..atm<br>\nJohn</p> → ..atm</p>
# Thanks\John</description> → </description>
# Yours sincerely,\nJ.D.Doe]]> → ]]>
_DEFAULT_MARKUP_TRAILING_SIGNOFF_TAG_RE = re.compile(
    r'(?:'
        r'<br\s*/?>\s*\n?\s*'
        r'|(?:Thanks|Cheers|Best|Regards|HTH|Yours\s+sincerely)\s*,?\s*[\n\s]*'
    r')'
    r'[A-Z][\w.]*(?:\s+[A-Z][\w.]*){0,2}'
    r'\s*(?=</p>|</description>|\]\]>)',
    re.IGNORECASE,
)


class Anonymizer:

    def __init__(self, dm_config: Dict[str, Any]=None):
        """
        Initialize the Anonymizer.
        """
        dm_config = dm_config or get_data_manager_config()

        data_manager_utils = dm_config.get("utils", {}) if isinstance(dm_config, dict) else {}
        anonymizer_config = data_manager_utils.get("anonymizer", {}) if isinstance(data_manager_utils, dict) else {}
        if not anonymizer_config:
            raise KeyError(
                "Anonymizer configuration not found under "
                "data_manager.utils.anonymizer or utils.anonymizer"
            )

        self.anonymizer_config = anonymizer_config
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
    
    def _discover_names(self, text: str) -> set:
        """NER to discover names in the text."""
        doc = self.nlp(text)
        return {
            ent.text for ent in doc.ents
            if ent.label_ == "PERSON" and ent.text not in self.EXCLUDED_WORDS
        }

    def _discover_names_markup(self, markup: str) -> set:
        # Full document: names with surrounding context (catches CDATA)
        full_text = self._extract_text(markup)
        names = self._discover_names(full_text)
        # Per-chunk: focused paragraphs (catches standalone names in <p>)
        for chunk in self._extract_text_chunks(markup):
            names |= self._discover_names(chunk)
        return names

    def anonymize(self, text: str) -> str:
        """
        Anonymize names, emails, usernames, greetings, and sign-offs from the text.
        """
        names_to_replace = self._discover_names(text)

        # Remove email addresses and usernames
        text = self.EMAIL_PATTERN.sub("", text)
        text = self.USERNAME_PATTERN.sub("", text)

        text = self._strip_greetings_signoffs(text)
        return self._replace_names(text, names_to_replace)
    
    def anonymize_markup(self, markup: str) -> str:
        """
        Anonymize names, emails, usernames, greetings, and sign-offs from the markup.
        including html, rss, and other markup formats. (especially twiki and discourse markup)
        """
        names_to_replace = self._discover_names_markup(markup)
        # Remove email addresses and usernames
        markup = self.EMAIL_PATTERN.sub("", markup)
        markup = self.USERNAME_PATTERN.sub("", markup)
        markup = _DC_CREATOR_RE.sub(r'\1\2', markup)
        markup = _DEFAULT_GENERIC_MARKUP_AUTHOR_ELEMENT_RE.sub("", markup)
        markup = _DEFAULT_GENERIC_MARKUP_USER_LINK_RE.sub("", markup)
        markup = _DEFAULT_MARKUP_SIGNOFF_TAG_RE.sub("", markup)
        markup = _DEFAULT_MARKUP_TRAILING_SIGNOFF_TAG_RE.sub("", markup)
        markup = _DEFAULT_MARKUP_TWIKI_USER_LINK_RE.sub("", markup)
        markup = self._strip_greetings_signoffs(markup)
        return self._replace_names(markup, names_to_replace)

    def _strip_greetings_signoffs(self, text: str) -> str:
        lines = text.splitlines()
        filtered = []
        for line in lines:
            stripped = line.strip()
            if any(p.match(stripped) for p in self.GREETING_PATTERNS):
                continue
            if any(p.match(stripped) for p in self.SIGNOFF_PATTERNS):
                continue
            filtered.append(line)
        return "\n".join(filtered)

    def _replace_names(self, text: str, names: set) -> str:
        for name in sorted(names, key=len, reverse=True):
            text = re.compile(r'\b' + re.escape(name) + r'\b', re.IGNORECASE).sub("", text)
        return "\n".join(line for line in text.splitlines() if line.strip()) 

    def _extract_text(self, markup: str) -> str:
        """Strip markup to plain text for NER. Format-agnostic."""
        attrs = " ".join(_ATTR_TEXT_RE.findall(markup))
        clean = _CDATA_RE.sub(" ", markup)
        clean = _TAG_RE.sub(" ", clean)
        clean = unescape(clean)
        return re.sub(r"\s+", " ", f"{clean} {attrs}").strip() 

    def _extract_text_chunks(self, markup: str) -> list:
        chunks = []
        # Text content from tags
        for match in _CONTENT_TAG_RE.finditer(markup):
            inner = _CDATA_RE.sub(" ", match.group(1))
            clean = _TAG_RE.sub(" ", inner)
            clean = unescape(clean).strip()
            if clean:
                chunks.append(clean)
        # Text from attributes
        attr_text = " ".join(_ATTR_TEXT_RE.findall(markup))
        if attr_text.strip():
            chunks.append(attr_text.strip())
        return chunks