# Evidence engine

The evidence engine answers two questions without any institutional data feed:

1. **Who are our researchers?** (people bootstrap)
2. **Which public records belong to them?** (authorship and link inference)

Each answer is an *assertion* with evidence a person can read (see [data-model.md](data-model.md)). The engine suggests; people decide.

## 1. People bootstrap

People enter Tessera from several routes. Each route has a different trust level.

| Route | Creates | Identity anchor | Initial status |
|---|---|---|---|
| **Roster file** (optional CSV) | Person + unit memberships | Roster local ID, email, ORCID | `active` |
| **Grant investigators** at the organization's configured names | Person + `investigator_on` links | NIH profile ID | `active` while they hold or recently held a grant there; otherwise `former` |
| **Affiliation sweep** of PubMed | Candidate person from a cluster of affiliated author records | ORCID when present, else the name cluster | `candidate`, shown in the *Discovered people* queue |
| **Study officials** at the organization | Candidate person, or a link to an existing person | Name + affiliation | `candidate` |
| **Manual entry** | Person | Whatever the admin provides | `active` |

Candidates become `active` when an admin promotes them, one at a time or in bulk with filters such as "at least 3 affiliated papers in the last 5 years". The promotion threshold is a setting, and automatic promotion is off by default.

**Merging.** When two records look like the same person (same ORCID, same NIH profile ID, or a strong name, coauthor, and topic match), the engine proposes a `same_as` assertion. Merges are reviewed and reversible.

## 2. Name handling

Names are the weakest identity signal, so they are handled carefully.

- Normalize Unicode (NFKD, strip diacritics for matching while keeping the original for display), case, punctuation, and hyphenation (`Smith-Jones` matches `Smith Jones` and `SmithJones`).
- Recognize family-name particles (`van`, `de`, `da`, `von`, `le`) and compound family names.
- Compare given names by **compatibility**, not equality: `J` is compatible with `Jane`; `Jane A` conflicts with `Jane B`; `Jane` conflicts with `Joan`.
- Keep known variants and former names in `person_name`. A curator can add one from the review screen.
- Estimate **name ambiguity** from the corpus: how many distinct authors with the same family name and first initial appear in the organization's publications. Common names need more evidence.

Tessera never infers gender, ethnicity, or nationality from names, and never uses them as features.

## 3. Candidate retrieval (person → publications)

For each active person, gather candidate publications from:

1. The **affiliation sweep** results whose author name is compatible with the person.
2. A **person search** in PubMed by name variants, limited to the years the person plausibly published (from their earliest known grant or paper minus a margin, up to now).
3. **Grant links:** publications linked to any grant the person is an investigator on.
4. **ORCID works** *(wave 2)*.

Retrieval recall is measured separately from scoring accuracy (see Evaluation). A paper that is never retrieved can never be suggested.

## 4. Scoring

Each (person, publication, author position) candidate gets a score from an **additive evidence model**: a sum of feature weights in log-odds, passed through a logistic function to give a 0–1 score. It is deliberately simple so that every point can be explained.

### Features in `authorship/v1`

| Feature | Fires when | Weight (log-odds) |
|---|---|---|
| Prior | Base rate for the retrieval route (affiliation sweep > grant link > name search) | −2.0 to +0.5 |
| ORCID match | The author's ORCID equals the person's confirmed ORCID | +8.0 (decisive) |
| ORCID conflict | The author has a *different* ORCID | −8.0 (decisive) |
| Email match | An email in the affiliation text matches a known email for the person | +6.0 |
| Organization affiliation | The author's affiliation on this paper matches the organization profile | +2.5 |
| Grant link | The paper acknowledges, or is linked to, a grant the person is an investigator on | +3.0 |
| Coauthor overlap | Coauthors appear on the person's accepted papers | +1.0 each, capped at +4.0 |
| Given-name agreement | Full given name matches | +1.5 |
| Middle-initial conflict | Both have middle initials and they differ | −3.0 |
| Given-name conflict | Full given names are incompatible | −6.0 |
| Topic fit | Similarity between the paper's MeSH terms and the person's accepted papers | 0 to +2.0 |
| Journal history | The person has accepted papers in this journal | +0.5 |
| Career window | The paper predates the person's estimated career start | −2.0 |
| Name ambiguity | The name is common in the corpus | 0 to −3.0 |
| Rejected-cluster signal | Coauthors mostly come from papers the person rejected | −1.0 to −2.0 |
| Author-slot conflict | Another person is a stronger match for the same author slot | −4.0 |

Weights in v1 are hand-set starting values. They are versioned with the method (`authorship/v1`), so every assertion records which weights produced it. Once enough local decisions exist, weights are fitted to them (a small logistic regression in pure Python, with no new dependencies) and released as `authorship/v2` with an evaluation report.

### Evidence feeds itself

Accepting a paper strengthens later scores: coauthors, topics, and journals from accepted papers become evidence for the rest. Rejections teach the engine which coauthor clusters belong to *someone else*. After each batch of decisions, the engine re-scores that person's open suggestions. It never touches decided ones.

### Bands and policy

| Band | Score | Default handling |
|---|---|---|
| **Strong** | ≥ 0.95 | Suggested at the top of the queue. Can be auto-accepted only if the admin enables auto-accept *and* the latest evaluation shows ≥ 99% precision at that threshold |
| **Possible** | 0.60–0.95 | Suggested; needs review |
| **Unlikely** | < 0.60 | Hidden by default; available behind a "show unlikely" toggle |

Thresholds are settings. Auto-accept is **off by default**.

## 5. Other link inference

| Link | Method | Evidence shown |
|---|---|---|
| Publication → grant (`acknowledges`) | Source-reported by the grant link table and the publication's own grant list. Core project numbers are normalized from free-text grant strings | Which source(s) reported it, and the raw grant string |
| Study → publication (`references`) | Source-reported by the registry | Reference type (result, background, derived) |
| Study → grant (`funded_by`) | Source-reported by the registry's NIH secondary IDs | The raw secondary ID |
| Person → study (`official_of`) | Name compatibility + affiliation matching the organization + the organization appearing as a site or sponsor; boosted when the person holds a grant the study cites | Each matched signal |

## 6. Curation workflow

- The **review queue** lists suggestions ordered by value: strong matches first for fast confirmation, then conflicts and ambiguous names.
- Each suggestion shows the paper, the matched author slot (highlighted in the author list), the score band, and the evidence items as plain sentences with their weights.
- Decisions: **accept**, **reject**, **defer**, **undo**, each with an optional note. Keyboard: `j`/`k` move, `a` accept, `r` reject, `d` defer, `u` undo.
- **Bulk actions** are allowed only within a person and band, such as "accept all strong suggestions for this person", and are logged per assertion.
- Every decision is a `curation_event`. A profile reflects it on the next request.

## 7. Evaluation

Tessera must be able to prove how well it matches, on the adopter's own data.

- `tessera eval authorship` compares current suggestions against a **gold set**: decisions curators have made, plus an optional adjudicated sample.
- Reports: precision, recall, and F1 by band; candidate retrieval recall; review burden (suggestions per accepted paper); and calibration (does 0.9 mean 90%?).
- **Slices:** common versus rare names, early-career versus established, people with versus without ORCID, transliterated names. Large gaps between slices are treated as bugs.
- **Hygiene:** hold out people, not just papers; use a time-based holdout for new publications; never evaluate on decisions that were fed back as features for the same item.
- Reports are written to `var/derived/eval/` with the method version and data timestamp.

### Targets (to validate during the pilot)

| Measure | Target |
|---|---|
| Precision of the *Strong* band | ≥ 99% |
| Candidate retrieval recall | ≥ 95% |
| Overall precision at the default review threshold | ≥ 90% |
| Median curator time per suggestion | < 5 seconds |
