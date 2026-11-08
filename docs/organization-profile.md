# Describing your organization

Tessera has no HR feed. It recognizes your organization in public records by how the organization's name is written there: in author affiliations on papers, on NIH grants, and in clinical trial registrations. The `[organization]` section of `tessera.toml` is that description, and it decides what Tessera finds.

A good profile finds nearly everything that belongs to you and very little that does not. This guide explains each setting, how to find the right values, and how to check the result.

## The settings

```toml
[organization]
name = "Example University"
short_name = "EXU"
aliases = [
  "Example University",
  "Example Univ",
  "Example University School of Medicine",
]
affiliation_patterns = ["example univ*", "exu medical center"]
reporter_org_names = ["EXAMPLE UNIVERSITY"]
ctgov_org_names = ["Example University", "Example University Medical Center"]
email_domains = ["example.edu"]
exclusions = ["Example University of Technology"]
```

| Setting | Used for | Notes |
|---|---|---|
| `name` | Display, and as an alias | Required |
| `short_name` | Display in the sidebar | Optional |
| `aliases` | Recognizing the organization in affiliations, sponsor and site names, and grant records; the PubMed affiliation sweep | Whole words, case-, accent-, and punctuation-insensitive |
| `affiliation_patterns` | The same, with wildcards | `*` matches any run of characters inside a word, so `example univ*` matches "Example Univ" and "Example University" |
| `reporter_org_names` | Finding grants in NIH RePORTER | Exactly as RePORTER writes the organization, usually in capitals |
| `ctgov_org_names` | Finding studies in ClinicalTrials.gov | Sponsor, collaborator, and site names; falls back to `aliases` |
| `email_domains` | Strong evidence that an author is yours | An address at the domain or a subdomain in an author's affiliation |
| `exclusions` | Rejecting look-alikes | A text that matches an exclusion never counts as the organization, even if it also matches an alias |

Matching ignores case, accents, punctuation, and hyphens, and works on whole words: `Example University` matches "EXAMPLE UNIVERSITY, Dept. of Medicine" but not "Examples University".

## Finding the right names

**Affiliations (`aliases`, `affiliation_patterns`).** Search [PubMed](https://pubmed.ncbi.nlm.nih.gov/) for your organization with the affiliation field, for example `"Example University"[ad]`, open a dozen recent papers, and note every form the name takes: abbreviations, former names, the medical school or hospital, and common misspellings. Include names of hospitals and institutes that belong to you, and leave out partners that do not.

**Grants (`reporter_org_names`).** Search [NIH RePORTER](https://reporter.nih.gov/) for a project you know is yours and copy the organization name exactly as it appears under *Organization*. Large organizations often have several (a university and its medical school, for instance); list each.

**Studies (`ctgov_org_names`).** Search [ClinicalTrials.gov](https://clinicaltrials.gov/) for a study you know and copy the sponsor and site names as written.

**Look-alikes (`exclusions`).** If another organization's name contains one of yours ("Example University of Technology" contains "Example University"), add it. You will often find these after the first sync, in papers that clearly are not yours.

## Checking the result

After `uv run tessera sync`:

- `uv run tessera runs` shows how many records each source returned. A RePORTER sweep that finds nothing usually means `reporter_org_names` does not match RePORTER's spelling.
- On the **Sources** page, the *Found* column shows the same counts, and the run notes say if PubMed or ClinicalTrials.gov reported anything unusual.
- The **Grants** list should show your active NIH awards; spot-check a few you know.
- **Discovered people** lists affiliated authors who are not in the directory. If many of them clearly belong to another organization, an alias or pattern is too broad: tighten it or add an exclusion.
- On a paper's page, the *Record* card shows which source it came from. An affiliated paper that is not yours points to a pattern to tighten.

Change the profile, then run `uv run tessera sync --full` to fetch again with the new names. Decisions you have already made are kept.

## How people are found

The directory starts from three sources:

1. **NIH grant investigators.** Contact principal investigators on your organization's own awards join as active members. Other investigators (on multi-PI grants and subprojects, often from other organizations) start as *discovered* people and are promoted automatically once at least `discover_min_papers` of their papers carry your affiliation with their full name.
2. **A roster**, if you import one (`uv run tessera people import roster.csv`). Roster people are members: active, or former once every membership the roster lists has ended. Someone who is also in the grant records under no shared identifier is proposed under **Possible duplicates** for a curator to merge.
3. **Affiliated authors** in PubMed who match nobody in the directory. They appear under **Discovered people** with their evidence, for a curator to promote or dismiss. Dismissed people are not proposed again.

Each person's papers are then found by searching PubMed for their name and scored with the evidence engine. See [Curating](curation.md) for how suggestions are reviewed.
