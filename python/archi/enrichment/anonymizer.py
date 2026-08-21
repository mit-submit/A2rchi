"""Text anonymizer for connector emission hooks (anonymize_data).

Provenance: ported from archi v2 ``dev@28b977d1``,
``src/data_manager/collectors/utils/anonymizer.py`` (authors: Pietro
Lugato, Hasan Ozturk). This closes the ``anonymize_data`` cutover gate
noted in the :mod:`archi.sources.jira` docstring: the v2 JIRA collector
optionally ran ``Anonymizer().anonymize(issue_text)`` before storage;
in v3 the same text->text pass is applied at emission over the
connector text surface — ``jira_issue.attrs`` text fields and
``document_chunk.attrs["text"]`` — before embedding.

Changes from the v2 original:

- No v2 config plumbing: the ``data_manager.utils.anonymizer`` config
  block became constructor parameters, with the v2 base-config
  template's defaults inlined (nlp model ``en_core_web_sm``; JIRA
  ``[~user]`` mention pattern as the username default).
- spaCy is imported lazily on first use instead of at module import,
  so importing this module (and the enrichment package) never requires
  spaCy. ``nlp_model=None`` disables NER entirely — the regex passes
  (emails, usernames, greetings/sign-offs, markup author elements)
  still run, plus any caller-supplied ``known_names``.
- ``known_names``: connectors usually know author/assignee names from
  record metadata; these are always redacted, with or without NER.
- ``download_missing_model`` (default True, matching v2's
  download-on-missing behavior) can be set False to fail fast instead
  of downloading a model mid-ingest.

Everything else — the markup regexes, greeting/sign-off stripping,
name replacement, and text extraction for NER — is kept verbatim.
"""
from __future__ import annotations

import re
from html import unescape
from collections.abc import Iterable, Sequence

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

# Defaults lifted from the v2 base-config template
# (src/cli/templates/base-config.yaml, dev@28b977d1).
_DEFAULT_NLP_MODEL = "en_core_web_sm"
_DEFAULT_EXCLUDED_WORDS = ("John", "Jane", "Doe")
_DEFAULT_GREETING_PATTERNS = (
    r"^(hi|hello|hey|greetings|dear)\b",
    r"^\w+,\s*",
)
_DEFAULT_SIGNOFF_PATTERNS = (
    r"\b(regards|sincerely|best regards|cheers|thank you)\b",
    r"^\s*[-~]+\s*$",
)
_DEFAULT_EMAIL_PATTERN = r"[\w\.-]+@[\w\.-]+\.\w+"
_DEFAULT_USERNAME_PATTERN = r"\[~[^\]]+\]"


class Anonymizer:
    """Redact names, emails, usernames, greetings, and sign-offs."""

    def __init__(
        self,
        *,
        nlp_model: str | None = _DEFAULT_NLP_MODEL,
        excluded_words: Iterable[str] = _DEFAULT_EXCLUDED_WORDS,
        greeting_patterns: Sequence[str] = _DEFAULT_GREETING_PATTERNS,
        signoff_patterns: Sequence[str] = _DEFAULT_SIGNOFF_PATTERNS,
        email_pattern: str = _DEFAULT_EMAIL_PATTERN,
        username_pattern: str = _DEFAULT_USERNAME_PATTERN,
        known_names: Iterable[str] = (),
        download_missing_model: bool = True,
    ) -> None:
        self._nlp_model = nlp_model
        self._download_missing_model = download_missing_model
        self._nlp = None
        self._nlp_loaded = nlp_model is None

        self.EXCLUDED_WORDS = set(excluded_words)
        self.GREETING_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in greeting_patterns]
        self.SIGNOFF_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in signoff_patterns]
        self.EMAIL_PATTERN = re.compile(email_pattern)
        self.USERNAME_PATTERN = re.compile(username_pattern)
        self.KNOWN_NAMES = {
            name.strip() for name in known_names if name and name.strip()
        }

    def _load_nlp(self):
        if self._nlp_loaded:
            return self._nlp
        import spacy

        try:
            self._nlp = spacy.load(self._nlp_model)
        except OSError:
            if not self._download_missing_model:
                raise
            spacy.cli.download(self._nlp_model)
            self._nlp = spacy.load(self._nlp_model)
        self._nlp_loaded = True
        return self._nlp

    def _discover_names(self, text: str) -> set:
        """NER (when enabled) plus known names present in the text."""
        names = {name for name in self.KNOWN_NAMES if name}
        nlp = self._load_nlp()
        if nlp is None:
            return names
        doc = nlp(text)
        names |= {
            ent.text for ent in doc.ents
            if ent.label_ == "PERSON" and ent.text not in self.EXCLUDED_WORDS
        }
        return names

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
