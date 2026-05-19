# Paper style notes

This guide is the source of truth for the writing voice of the Archi
CHEP 2026 paper. The conventions below were extracted from a study of
high-impact CHEP and J. Phys. Conf. Ser. papers — Rucio, PanDA-Rubin,
HEPScore23, the four CHEP 2024 CMS Submission Infrastructure papers,
and CHEP 2023 proceedings — and are matched to that corpus, not to a
generic "academic paper" prior.

The full corpus analysis lives in
`openspec/changes/plan-chep-paper-outline/design.md` §1. This note is
the operational summary co-authors need at the keyboard.

## 1. Sentence patterns to use

**Section opener.** State a fact about the system, the experiment, or
the deployment. Do not summarise what the section will do. The two
patterns observed in the corpus:

- **Pattern A**: `<Named system> is <one-line definition>.`
  Example (Rucio): "Rucio is an open-source software framework that
  provides scientific collaborations with the functionality to organize,
  manage, and access their data at scale."
- **Pattern B**: `<Experiment / scale fact / date>` followed by a
  named-system sentence.
  Example (HEPScore23): "In April 2023, HEPScore23, the new benchmark
  based on HEP specific applications, was adopted by WLCG, replacing
  HEP-SPEC06."

**Subsection opener.** Name the component or claim. No "In this
subsection".

**Figure introduction.** Two patterns dominate:

- "Figure $N$ shows $X$."
- "$X$, as shown in Figure $N$, ..."

Not used: "We present in Figure $N$...", "Below we depict...".

**Numbers.** State the specific number, not a category. "144 questions"
not "many questions". "87 tok/s" not "high throughput". "260 questions"
not "a curated dataset".

**Acronyms.** Expand on first use, including the experiment name:
"Compact Muon Solenoid (CMS)", "Computing in High Energy Physics
(CHEP)", "retrieval-augmented generation (RAG)", "large language model
(LLM)", "mixture of experts (MoE)".

## 2. Banned words and constructions

These are absent from the CHEP corpus. Do not use:

| Banned                       | Use instead             |
|------------------------------|-------------------------|
| leverage                     | use                     |
| comprehensive, robust, novel | (delete; show by data)  |
| delve, delve into            | describe, examine       |
| seamless, seamlessly         | (delete)                |
| harness, unlock, empower     | (delete)                |
| cutting-edge                 | (delete or be specific) |
| paradigm shift               | (delete)                |
| first-of-its-kind            | (delete)                |
| furthermore, moreover        | (delete; new sentence)  |
| additionally                 | (delete)                |
| It is important to note that | (delete)                |
| In conclusion (as opener)    | (delete)                |

Other rules:

- "this work" appears at most once per section.
- "We propose to..." is not a paragraph opener.
- Decorative em-dashes are forbidden. Em-dashes are allowed where they
  replace a colon or a parenthesis, not as connective tissue. If a
  sentence has more than one em-dash, rewrite it.
- Bullet lists do not appear in narrative sections. Lists belong in
  tables. The Abstract, Introduction, Architecture body, Evaluation
  body, and Conclusion must not contain `\begin{itemize}` or
  `\begin{enumerate}`.

When a CHEP author means "use", they write "use".

## 3. Tense and voice

- **Present indicative** for in-production system description: "Archi
  runs in production at CERN.", "The data manager ingests sources into
  a vector store."
- **Past tense** for deployment milestones and experiments: "We deployed
  Archi for CompOps in [DATE].", "Each judge was run twice with
  different run-ids."
- **Future tense** only for forthcoming work in the conclusion's
  outlook sentence and nowhere else.
- **First person plural** ("we") used sparingly and only with concrete
  operational verbs: "we deployed", "we evaluated", "we extracted".
  Not "we propose", not "we will explore".
- "Will" is not a hedge. Do not write "this will likely improve".

## 4. Citation density

- **Introduction**: 1--2 citations per paragraph.
- **Architecture**: 0--1 citations per paragraph.
- **Operational Experience and Evaluation**: near-zero citations except
  where a comparison to prior work or a method is the explicit subject
  of the sentence (e.g., the rubric methodology).
- Cite specific software versions where they affect reproducibility
  (LangChain, LangGraph, Ollama, OpenRouter, model identifiers).
- Cite specific dates where they affect reproducibility (deployment
  date, evaluation run date).

Target final reference count: 12--18 entries. Numbered `[1]` inline,
IOP/EPJ house style.

## 5. Section structure

The paper has seven numbered sections plus abstract, acknowledgements,
and references. The list:

1. Introduction
2. The CMS Computing Operations Workload (domain, question patterns, agent requirements, common workflows)
3. The Archi Agent (architecture)
4. Curated Question Set (dataset)
5. Evaluation
6. Discussion
7. Conclusions and Outlook

The Discussion section covers forward-looking content (routing,
portability, judge limitations) that does not fit in the
single-paragraph conclusion. There is no Related Work section;
citations to prior work fold into the Introduction. The Discussion
section is a deliberate deviation from the "no Discussion section"
rule observed in the CHEP corpus, captured in
`openspec/changes/add-paper-requirements-workflows-and-discussion/`.

## 6. Conclusion rule

The conclusion is **one paragraph** of 4--6 sentences. It states what
was deployed, names the dataset and methodology, gives the headline
finding in one sentence, and ends with one ongoing direction in the
future tense. It does not contain a "future work includes" laundry
list of three or more items.

## 7. Verification

Each section's prose passes only after all of:

1. **Banned-word grep is clean.** Run:
   ```
   grep -i -n -E '\b(leverage|comprehensive|robust|novel|delve|seamless|harness|unlock|empower|cutting-edge|furthermore|moreover|additionally|in conclusion)\b' paper/<file>.tex
   ```
2. **Structural grep is clean.** Run:
   ```
   grep -n 'In this section\|In this subsection\|We propose to' paper/<file>.tex
   ```
3. **The rendered PDF for the section is read end-to-end.** Code
   reading is insufficient. Reviewers open the PDF, read the section,
   and check that the opening sentence states a fact and the conclusion
   is a single paragraph.
4. **Page-budget check.** The section's rendered length matches the
   budget in the design document within ±20%.

## 8. Upstream OpenSpec dependencies

Numbers and methodology in this paper come from in-flight OpenSpec
changes. Check their status before drafting:

- `design-chep-evaluation` — multi-judge methodology, judge model list,
  GPT-5 ceiling baseline, anti-length-bias rubric.
- `fix-chep-eval-confounds` — conditional rubric dimensions
  (groundedness only when retrieval present, refusal_appropriateness on
  live-access questions).
- `update-submit75-local-eval-matrix` — the local evaluation
  configuration matrix that drives the §5 result tables.
- `plan-chep-paper-outline` — this paper's section structure and
  paragraph plan; this style note is its operational summary.

## 9. Build

```
cd paper
pdflatex main.tex && bibtex main && pdflatex main.tex && pdflatex main.tex
```

The PDF is `paper/main.pdf`. Read it.

## 10. Lessons from the Memex corpus pass (2026-05)

A literature pass over the Memex OKG (generation 8, 2026-05-07),
extended with web search, compared the Archi draft against three
corpora: HEP/CHEP writing-voice analogs (Rucio 2014; CMS workload
2020; ATLAS Distributed Computing 2015; ATLAS analytics 2016),
deployed-LLM-agent subject-matter analogs (CodeReAct 2026; Carbon to
Diamond 2021; Dong/SIGMOD 2024; TruthReader 2024; Wang/NAACL 2025),
and HEP-LLM-assistant priors at CHEP or CERN (AccGPT 2025; chATLAS
2025). Provenance for the pass is in
`openspec/changes/revise-chep-paper-from-corpus-analogs/`.

Six rules came out of the pass.

### 10.1 Introduction closes with one contract sentence

Five "Section X describes Y" sentences read as scaffolding from an
earlier draft. The corpus (Rucio 2014; CMS workload 2020) closes the
introduction with one sentence enumerating what the paper covers.
Name §3, §4, §5 explicitly; §2 is implicit in the §1 framing and §6
needs no pointer.

### 10.2 Abstract surfaces a category-dependent finding alongside the headline number

An abstract-only reader leaves with both the headline rubric score
and one finding that contextualises it. CodeReAct 2026 and
Dong/SIGMOD 2024 lead with the gotcha. For Archi, the contextualising
finding is the no-tools-beats-agent inversion on doc-answerable
questions.

### 10.3 Figure captions describe; they do not interpret

≤ 2 sentences per `\caption{...}`. Axes, marker encoding, what is
plotted: that is the caption. Interpretation ("the agent matches the
reference") belongs in the prose paragraph that anchors the figure.

### 10.4 Operational-experience numbers are load-bearing

The deployment date, the operator-message count, and the
distinct-conversation count are evidence the system has been used by
real operators for a measurable window. Put one of them in §1
paragraph 1 or 2 (or in the abstract); a reader who reads only the
abstract and §1 should encounter at least one. CodeReAct 2026 and
Carbon to Diamond 2021 lead with this kind of evidence.

### 10.5 §5 paragraphs that report an inversion open with the inversion

When a paragraph reports a counter-intuitive finding — the no-tools
agent matching or beating the full agent on doc-answerable questions;
the RAG pipeline beating the agent on source faithfulness — the
inversion is the opening sentence, not the second beat. The opening
of a paragraph is the high-recall position; reserve it for the
finding.

### 10.6 When §5 reports a source-faithfulness inversion, name the mechanism

The agent synthesises across many tool returns and surrenders the
tightly-cited-passage advantage. Cite the per-pipeline numbers (4.62
RAG, 3.78 agent) in the prose, not only in the figure caption (rule
10.3 trims the captions).
