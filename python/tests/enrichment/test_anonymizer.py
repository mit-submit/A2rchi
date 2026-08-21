"""task.w2.enrichment — anonymizer text->text pass, offline.

Runs with NER disabled (``nlp_model=None``) so no spaCy model is
needed: the regex passes plus ``known_names`` cover the connector
emission hook (jira/docs ``anonymize_data``) deterministically.
"""
from archi.enrichment.anonymizer import Anonymizer


def _anonymizer(**kwargs):
    kwargs.setdefault("nlp_model", None)
    return Anonymizer(**kwargs)


def test_redacts_email_name_and_jira_mention():
    an = _anonymizer(known_names=["John Doe"])
    text = (
        "Hi all,\n"
        "John Doe saw transfer failures at T2_US_MIT.\n"
        "Contact jdoe@cern.ch or [~jdoe] about run 381000.\n"
        "Cheers,\n"
        "the ops team\n"
    )
    out = an.anonymize(text)
    assert "jdoe@cern.ch" not in out
    assert "John Doe" not in out
    assert "[~jdoe]" not in out
    # Greeting and sign-off lines are stripped.
    assert "Hi all" not in out
    assert "Cheers" not in out
    # Operational content survives.
    assert "T2_US_MIT" in out
    assert "run 381000" in out


def test_known_names_case_insensitive_word_bounded():
    an = _anonymizer(known_names=["Jane Roe"])
    out = an.anonymize("jane roe and JaneRoeography met.")
    assert "jane roe" not in out.lower().replace("janeroeography", "")
    assert "JaneRoeography" in out  # word boundary respected


def test_markup_pass_strips_author_constructs():
    # Content paragraphs must exceed three words: the ported v2 markup
    # signoff heuristic removes any <p> of up to three capitalized-ish
    # words (case-insensitive), by design.
    an = _anonymizer()
    markup = (
        '<p>Transfer failures were observed at T2_US_MIT overnight</p>'
        '<a class="twikiLink" href="/twiki/bin/view/Main/JohnDoe">JohnDoe</a>'
        '<span class="author">Jane Roe</span>'
        '<p>Please contact jdoe@cern.ch for any further details</p>'
    )
    out = an.anonymize_markup(markup)
    assert "JohnDoe" not in out
    assert "Jane Roe" not in out
    assert "jdoe@cern.ch" not in out
    assert "Transfer failures were observed at T2_US_MIT overnight" in out
    assert "for any further details" in out


def test_construction_never_imports_spacy_eagerly():
    # Default model configured, but nothing loads until first use.
    an = Anonymizer()
    assert an._nlp is None and an._nlp_loaded is False
    # NER-disabled instances never load a model at all.
    off = _anonymizer()
    assert off._nlp is None and off._nlp_loaded is True
