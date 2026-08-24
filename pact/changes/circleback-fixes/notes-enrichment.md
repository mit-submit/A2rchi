# circleback-fixes: enrichment stack behavior changes

Fixes for the confirmed adversarial-review findings in
`python/archi/enrichment/` (branch `cb-fix-enrichment`, base
`728739e6`). Each entry: the finding, the fix, the resulting behavior
change, and whether the changed behavior is shared with v2/canonical
(`shared_with_canonical`) — i.e. whether the same defect exists in the
v2 anonymizer defaults or the okg-deployments port source and needs
coordination there too.

Verification for every entry:
`/work/submit/lavezzo/okg-venv/bin/python -m pytest python/tests/enrichment/ -q`
(35 passed).

## 1. Email redaction left local-part fragments (MAJOR)

- **Finding:** `anonymizer.py` default email pattern
  (`[\w\.-]+@[\w\.-]+\.\w+`) covered only the RFC-simple core:
  `john.doe+ops@cern.ch` left `john.doe+` behind (the name),
  `o'brien@cern.ch` left `o'`, and percent-encoded addresses in URLs
  (`mail=john.doe%40cern.ch`) were untouched.
- **Fix:** `_DEFAULT_EMAIL_PATTERN` now matches quoted local parts and
  RFC-5322 atext (`+` tags, apostrophes, `%`-encoded octets) and
  accepts `%40` as the separator. URL-structural characters (`/ = ?`)
  are deliberately excluded from the local-part class so a surrounding
  URL path/query is not swallowed.
- **Behavior change:** addresses that previously left local-part
  fragments are now removed whole; `%40`-form addresses inside URLs are
  now redacted (the rest of the URL survives). Callers passing a custom
  `email_pattern` are unaffected.
- **Test:** `test_anonymizer.py::test_email_local_part_never_leaks_fragments`
- **shared_with_canonical:** yes — the default pattern was lifted
  verbatim from the v2 base-config template (`dev@28b977d1`); v2
  deployments leak the same fragments.

## 2. Encoded/NBSP variants bypassed discovery-vs-replacement (MAJOR)

- **Finding:** name discovery ran on unescaped text while replacement
  ran on the raw input, so `John&nbsp;Doe`, `John\xa0Doe`, and
  `jdoe&#64;cern.ch` survived both the name and email passes.
- **Fix:** new `_normalize_encodings()` runs at the top of both
  `anonymize()` and `anonymize_markup()`, before discovery and every
  redaction pass: decodes NBSP (U+00A0, `&nbsp;`, `&#160;`), numeric
  character references (`&#64;`/`&#x40;` → `@`, ...), and the safe
  named entities `&amp; &apos; &quot; &commat;`. References that would
  decode to `<` or `>` are left encoded, so the markup pass sees
  unchanged tag structure and the author-element regexes keep their
  offsets/behavior.
- **Behavior change:** encoded occurrences of names/emails are redacted
  like their plain forms. Output text now carries decoded entities
  (`R&amp;D` → `R&D`, NBSP → space) — a visible normalization of the
  emitted text surface. `&lt;`/`&gt;` remain encoded.
- **Test:** `test_anonymizer.py::test_encoded_and_nbsp_variants_redacted`
- **shared_with_canonical:** yes for the text pass (v2's `anonymize`
  has the same discovery/replacement mismatch via its NER path); the
  markup-pass plumbing it also protects is v3-new.

## 3. NER-off author formats outside four markup shapes leaked (MAJOR)

- **Finding:** in NER-disabled mode (the deterministic connector mode),
  only four hardcoded markup shapes were stripped. Leaked: non-CDATA
  `<dc:creator>Name</dc:creator>`, `<a href="mailto:...">John Doe</a>`
  anchor text, and TWiki signatures `-- Main.JohnDoe - 2024-01-15`.
- **Fix:** three new patterns — `_DC_CREATOR_PLAIN_RE` (dc:creator with
  or without CDATA, applied after the verbatim CDATA rule),
  `_DEFAULT_MARKUP_MAILTO_LINK_RE` (removes the whole mailto anchor,
  whose href the email pass has already emptied), and
  `_TWIKI_SIGNATURE_RE` (`-- Main.WikiWord [- date]` lines, also
  `TWiki.WikiWord`), the last applied in both the text and markup
  passes since TWiki signatures appear in plain topic text.
- **Behavior change:** those three author formats are now redacted with
  NER off (and on). dc:creator elements are emptied, mailto anchors
  removed entirely, signature lines dropped.
- **Test:** `test_anonymizer.py::test_ner_off_author_shapes_redacted`
- **shared_with_canonical:** no — the markup/NER-off/known_names
  machinery is v3-new (v2 always ran spaCy NER).

## 4. Greeting/sign-off filters destroyed operational content (MAJOR)

- **Finding:** the default greeting rule `^\w+,` deleted any line whose
  first word had a trailing comma ("However, run 381000 was affected
  badly."), and the sign-off rule prefix-matched content lines
  ("Regards to whoever fixed run 381000", "Thank you note was filed as
  CMSCOMPPR-1.").
- **Fix:** greetings now require an actual greeting word
  (`hi|hello|hey|greetings|dear|ciao|salut|hiya|howdy|good morning/…`;
  v2-compatible prefix semantics for those words, the bare `^\w+,` rule
  is gone). Sign-offs must be the entire line: the phrase, optional
  punctuation, and at most a short (≤4-word) trailing name introduced
  by punctuation.
- **Behavior change:** operational lines that merely start with a
  comma'd word or a sign-off word survive; real greeting/sign-off lines
  (including new phrases: `thanks in advance`, `take care`, `hth`, …)
  are still stripped. Lines like a bare salutation name ("John,") are
  no longer caught by the greeting filter — names are the
  known_names/NER layer's job. Callers passing custom
  `greeting_patterns`/`signoff_patterns` are unaffected.
- **Tests:**
  `test_anonymizer.py::test_operational_lines_survive_greeting_signoff_filters`
  and `test_anonymizer.py::test_real_greetings_and_signoffs_still_stripped`
- **shared_with_canonical:** yes — both default pattern sets came from
  the v2 base-config template; v2 deployments destroy the same
  operational lines.

## 5. `rules_files=None/[]` suppressed the packaged rule (MINOR)

- **Finding:** `declarative.py` used a key-presence check, so
  `GlobalTagReleaseLinker(rules_files=None)` (or `[]`) suppressed the
  packaged default and silently loaded all 104 substrate ontology rules
  under the cms linker name.
- **Fix:** None/empty `rules`/`rules_files`/`rules_dir` are treated as
  absent: the falsy keys are dropped and the packaged
  `global_tag_release.yaml` is used. Non-empty caller sources still
  win.
- **Behavior change:** deployment configs that render optional keys as
  null/empty now get the one packaged rule instead of the full
  substrate rule set.
- **Tests:**
  `test_defaults.py::test_global_tag_release_linker_treats_empty_rule_sources_as_absent`
  and `test_defaults.py::test_global_tag_release_linker_honors_explicit_rule_sources`
- **shared_with_canonical:** no — the packaged-default fallback is
  v3-new (the okg-deployments original hardcoded a deployment-relative
  path and had no such guard).

## 6. Dataset alias case-folding + unbounded negative cache (MINOR)

- **Finding:** `alias.py` `_norm()` lowercased dataset keys, so
  case-distinct canonical ids (`/A/B/RAW` vs `/a/b/raw`) collapsed to
  whichever loaded last; and `_resolve_dataset` cached every miss
  forever (a cache that memoized nothing — its fallback lookup hit the
  same index).
- **Fix:** dataset matching is exact-case; misses are no longer cached
  (`_dataset_by_value` holds only live nodes, so it is bounded by the
  live dataset population). Per-type case policy:
  - `cms_dataset` — **case-sensitive**: DBS dataset paths are
    case-sensitive identifiers; folding merges distinct datasets.
  - `cms_site`, `cmssw_release`, `cms_jira_key`, `global_tag`,
    `cms_run_number`, `cms_workflow`, `cms_hostname`/endpoints —
    **case-insensitive**: canonical forms are unique up to case and
    human transcription varies it (`t2_us_mit`, `cmssw_15_0_15`,
    `EOSCMS.cern.ch`), so case is noise, not identity.
- **Behavior change:** case-mismatched dataset mentions no longer
  resolve (previously they resolved, sometimes to the wrong node);
  memory no longer grows with unresolved dataset mentions.
- **Tests:** `test_alias.py::test_dataset_alias_is_case_sensitive`,
  `test_alias.py::test_non_dataset_matching_stays_case_insensitive`,
  `test_alias.py::test_dataset_misses_are_not_cached`
- **shared_with_canonical:** yes — both defects are verbatim from the
  port source (`cms/cms_sources/alias.py`, okg-deployments
  `main@f33a9c4`); the fix should be coordinated upstream.

## 7. Catalog `cms_run_number` pattern is context-free (NOTE-ONLY)

- **Finding:** `defaults/identifier_patterns.yaml` carries
  `\b[1-9][0-9]{5,6}\b` for `cms_run_number` — any bare 6/7-digit
  number matches. The packaged extraction rule kept a `run` context
  anchor (`\b(?:run|Run|RUN)\s*#?\s*([1-9][0-9]{5,6})\b`); the catalog
  pattern dropped it.
- **Decision: not fixed here.** The file is documented verbatim from
  okg-deployments `main@f33a9c4` and is loaded by
  `okg catalog load --apply` into `okg.identifier_patterns`, consumed
  by okg-side extractor/MCP tools whose regex-flavor and
  match-extraction conventions this repo does not control. The other
  catalog patterns match the identifier itself as the whole match (no
  capture groups); a `run`-prefixed pattern would change the matched
  identifier text unless the consumer honors capture groups, and a
  lookbehind workaround may not survive a non-Python regex engine
  (e.g. Postgres POSIX). Restoring the anchor is an okg-deployments
  catalog change, coordinated with its consumers.
- **shared_with_canonical:** yes — the pattern is the canonical
  okg-deployments catalog entry; the fix belongs upstream.

## Residual (out of the reviewed findings' scope)

- Reviewer repro G — a known name split across a line wrap
  (`John\nDoe`) is not matched by the word-bounded single-line name
  regex. Not in the confirmed findings list; unchanged.
- SQL enrichers' dedupe-key re-derivation and evidence-based
  derived-edge retraction (E5/E6) are okg-side contract items;
  untouched per scope.
