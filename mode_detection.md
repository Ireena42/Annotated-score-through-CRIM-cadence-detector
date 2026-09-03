# Mode detection: methodology and results

Consolidated writeup of every piece of the mode/modal-family detection
work in this app — the theoretical grounding, the two-stage method
(finalis → modal family), the cantus-mollis transposition problem and
its resolution, and the corpus-wide verification. Written to be usable
directly in the thesis, not just as a dev log (see `finalis_findings.md`
in this repo for the dated, blow-by-blow bug history this distills).

Scope note: everything here identifies a **modal family**, not a fully
disambiguated mode (Dorian vs. Hypodorian, authentic vs. plagal). Why
that's the right scope, not a shortcut, is answered directly in
["What "modal family" actually claims"](#what-modal-family-actually-claims)
below.

## The corpus

7 collections, all Humdrum-family (kern or MEI) encodings of
Renaissance polyphony, enumerated through `corpus_sources.
build_browse_index()`:

| Collection | Pieces | Source |
|---|---|---|
| `music21` (Palestrina + Monteverdi) | 1,370 | bundled with music21 |
| `jrp` (Josquin Research Project) | 1,338 | JRP GitHub |
| `1520s` | 667 | CRIM-adjacent Humdrum GitHub collection |
| `tasso` | 503 | Humdrum GitHub |
| `crim` | 309 | CRIM Project MEI |
| `lassus_psalms` | 50 | Humdrum GitHub |
| `seils` | 30 | Humdrum GitHub |
| **Total** | **4,267** pieces with a determinable finalis (4,319 total rows; 52 recorded `finalis: null`, genuinely undeterminable) | |

## Stage 1 — Finalis determination

Before any modal classification, each piece needs its **finalis** (the
final resting pitch class) determined. This is done by
`compute_finalis()` in [`scripts/precompute_finalis.py`](scripts/precompute_finalis.py:270),
run once per piece and cached to [`data/finalis.jsonl`](data/finalis.jsonl).

### Three independent signals, cross-checked

1. **`Low`** — the lowest sounding pitch at the piece's very last
   detected cadence (the *clausula basizans* convention: the bass
   voice's characteristic descending-fifth/rising-fourth approach to
   the final). Computed via CRIM Intervals'
   [`ImportedPiece.cadences(voice_detail=True, include_final=True)`](https://github.com/HCDigitalScholarship/intervals).
2. **`Tone`** — the same final cadence's *Cantizans* goal note (the
   upper-voice resolution), independent of what the bass actually did.
3. **`piece.final()`** — CRIM's own crude fallback: the literal last
   note sounding in the piece, no cadence detection at all.

**Why three, and why not just trust `Low`:** the original single-signal
design (`Low`, falling back to `.final()` only when zero cadences were
detected) was checked against real pieces and found wrong in two
independent, confirmed ways (see `finalis_findings.md` #2, #3 for the
full traces):

- An **evaded Bassizans** (the bass voice deliberately breaking its
  expected cadential resolution, a real 16th-century contrapuntal
  device — see Meier below) corrupts `Low` even when the cadence as a
  whole looks fully resolved. `Tone` isn't affected, because it reads
  the Cantizans, not the bass.
- **Desynchronized voice-part encodings** (confirmed directly: several
  Monteverdi files in this bundled corpus have voice-parts that simply
  stop having content before the piece's real ending) corrupt both
  `Low` and `Tone` together, since both come from the same (possibly
  truncated) detected cadence. `piece.final()`, reading the raw score
  content rather than a detected cadence, is more robust to this
  specific failure.

A genuinely random 24-piece validation sample (`finalis_findings.md`
#9) additionally found that `Low == Tone` is **not** two independent
opinions — both come from the same detected cadence — and found 3 real
cases where they agreed with each other and were both wrong together,
because the cadence they came from wasn't actually the piece's true
final one (an undetected tail passage, or an evaded gesture nearer the
real end). This is why agreement is checked categorically (does a
*second, genuinely independent* signal also agree) rather than by any
numeric threshold on the cadence detector's own confidence columns —
a threshold was tried and explicitly rejected: 7 correctly-confident
pieces in the same sample shared an identical `ToNext` value with one
of the 3 misses, so no cutoff could separate them.

### Confidence tiers

| Tier | Meaning |
|---|---|
| `confident_unanimous` | all 3 signals available, all agree |
| `confident_majority` | `Low`/`Tone` don't count as 2 independent votes when equal (see above); this tier needs either `Low != Tone` with 2 of the available signals agreeing, or `Low == Tone` with `piece.final()` unavailable (nothing to contradict the one opinion, but no second one either) |
| `low_confidence_split` | `Low == Tone` but `piece.final()` disagrees — the exact pattern the 24-piece sample found silently mis-scored before this fix — or no two signals agree at all |
| `single_signal` | only one signal could be computed |
| `part_duration_mismatch` | overrides all of the above when the piece's own voice-parts don't reach the same total duration (encoding-integrity problem that can corrupt every signal at once) |
| `error` | no signal produced a usable pitch class |

Validated against 39 hand-checked pieces across two independent random
samples — **100% correct on the confident tiers** — before the full
corpus was (re)computed.

**Full corpus, by tier** (4,319 records total):

| Tier | Count |
|---|---|
| `confident_unanimous` | 2,959 |
| `low_confidence_split` | 710 |
| `confident_majority` | 465 |
| `single_signal` | 105 |
| `error` | 52 |
| `part_duration_mismatch` | 28 |

3,424 of 4,267 pieces with a real finalis (80.2%) sit in one of the two
confident tiers. The Browse tab's "only high-confidence results" option
filters to exactly these two tiers.

## Stage 2 — From finalis to modal family

### What "modal family" actually claims

The app deliberately reports a **modal family** — one of 6 groups —
rather than a fully disambiguated mode (e.g. "Hypodorian" specifically,
as opposed to "Dorian"). Two independent reasons for that scope, not
one:

1. **This pipeline has no ambitus/range analysis.** `compute_finalis()`
   only ever determines the finalis *pitch*. Authentic-vs-plagal
   disambiguation (mode 1 vs. 2, 3 vs. 4, ...) in 16th-century theory
   is a question about the melodic *range* used around the final, which
   this app does not measure. Reporting "Hypodorian" outright would
   claim a precision the underlying computation doesn't support;
   "Protus family" is the level the data actually backs.
2. **Daniel C. Tompkins**, ["A Cluster Analysis for Mode Identification
   in Early Music Genres"](https://link.springer.com/chapter/10.1007/978-3-319-71827-9_24),
   in *Mathematics and Computation in Music* (MCM 2017), Lecture Notes
   in Computer Science vol. 10527 (Cham: Springer, 2017) — ran k-means
   clustering of pitch-class key-profiles on this *same*
   music21-bundled Palestrina corpus and found only **5** statistically
   distinct clusters (Dorian/D, Phrygian/E, Mixolydian/G, Ionian/C,
   Aeolian/A), not the full theoretical set of 8/12 authentic+plagal
   pairs. The actual note content doesn't reliably distinguish finer
   than family in this repertoire, independent of what this app's own
   pipeline can or can't measure.

The 6-family scheme (**Protus/Deuterus/Tritus/Tetrardus** — the 4
traditional finals D/E/F/G — plus **Ionian/Aeolian**, Glarean's own two
"added" finals C/A) is standard 16th-century theory, not invented for
this app: Heinrich Glarean, *Dodecachordon* (Basel, 1547), the
foundational source for the 12-mode system (8 traditional modes +
Glarean's addition of Ionian/Hypoionian and Aeolian/Hypoaeolian).

```python
_MODAL_FAMILY_BY_FINAL = {
    'D': 'protus', 'E': 'deuterus', 'F': 'tritus', 'G': 'tetrardus',
    'C': 'ionian', 'A': 'aeolian',
}
```

**Tritus (F-final) carries its own caveat**, shown in the app whenever
selected: Tompkins' same clustering found F-final pieces in this
corpus don't form their own statistical cluster at all — they fall
into the Ionian one, because the sharp 4th that theoretically defines
Lydian gets lowered by *musica ficta* too consistently for Lydian to
read as distinct from Ionian in the actual notes. The family is still
reported as Tritus (it matches what's actually encoded and what the
finalis computation actually measures), with the caveat surfaced
rather than the two families silently merged — so a result set
including Tritus pieces isn't read as a stronger claim than the pitch
content supports.

## Stage 3 — The transposition problem (cantus mollis)

### The question this answers

*If a piece has an F finalis and a flat in the key signature, is it
really "Tritus" (Lydian family), or does the flat mean it's actually
functioning as a transposed Ionian piece?* And the sharper follow-up:
*if a piece has a G finalis, that alone is compatible with Tetrardus
(Mixolydian), Hypodorian, or a transposed Dorian — is that ambiguity
actually resolved, or is the filter just re-labeling the same
uncertainty?*

**Short answer: resolved, not just relabeled.** A flat in the key
signature is not a stylistic ornament — it mechanically changes which
actual pitches the piece uses (B♭ instead of B♮ throughout), which
changes the pattern of whole- and half-steps around the finalis. That
pattern, not the bare finalis letter, is what determines the modal
family. So for a *given* piece, checking whether it carries a flat
signature is not a guess between candidate families — it reads off
which family the actual notes belong to.

### The mechanism

One flat in the signature (*cantus mollis*, as opposed to the
signature-less *cantus durus*) transposes the **whole diatonic
collection** up a fourth (equivalently, down a fifth). A piece under
mollis with final X sounds exactly like the *untransposed* mode on
(X − a fourth), just moved. This is standard 16th-century theory, not
invented here.

**Primary source, checked directly (not from a secondary summary):**
Harold S. Powers,
["Tonal Types and Modal Categories in Renaissance Polyphony,"](https://doi.org/10.1525/jams.1981.34.3.03a00030)
*Journal of the American Musicological Society* 34, no. 3 (1981):
428–470. Powers cites Siegfried Hermelink's *Dispositiones modorum*
(Tutzing, 1960) — an etic study of **Palestrina's own** tonal types
specifically (not read independently here, only as quoted in Powers) —
which classifies a cantus-mollis, G-final Palestrina piece as
**"Hypodorian"** (mode 2, transposed up a fourth from D). That is
exactly the G→Protus reclassification this app makes, independently
attested for this composer by name, not assumed by analogy from
generic theory.

Concretely, for the 5 finals this corpus actually uses under mollis:

| Final (mollis) | Sounds like (untransposed) | → Family |
|---|---|---|
| G | D-Dorian | Protus |
| F | C-Ionian | Ionian |
| D | A-Aeolian | Aeolian |
| C | G-Mixolydian | Tetrardus |
| A | E-Phrygian | Deuterus |

```python
_MOLLIS_TRANSPOSITION = {
    'G': 'protus', 'F': 'ionian', 'D': 'aeolian', 'C': 'tetrardus', 'A': 'deuterus',
}

def _modal_family_key(finalis_pitch, flats=0):
    if flats == 1 and finalis_pitch in _MOLLIS_TRANSPOSITION:
        return _MOLLIS_TRANSPOSITION[finalis_pitch]
    return _MODAL_FAMILY_BY_FINAL.get(finalis_pitch, 'nonstandard')
```

**E is deliberately excluded** from the mollis table: E minus a fourth
is B, and B-final (Locrian) was never a usable mode in 16th-century
practice — its fifth above the final is diminished, not perfect. An
E-final piece with a flat therefore doesn't transpose onto any real
family this way and is left as plain Deuterus, unreclassified.

### What was checked, and what was deliberately left open

Powers' own "tonal type" is explicitly **three** markers, not the two
used here: system (durus/mollis) + **cleffing** (chiavette/"high
clefs" vs. standard/"low clefs") + final. This app's transposition
logic uses only system and final, dropping cleffing — checked directly
against Powers before shipping this, not assumed away (see
`always-cite-new-concepts`/`check-simplifications-against-full-source`
project memory: this exact gap was the reason for that re-check):

- Read Powers' own worked examples (his mode 5/6 tables, and the
  Hermelink Hypodorian case above) closely enough to see that where
  cleffing *does* vary, its documented role is distinguishing
  **authentic from plagal** within one family (mode 5 vs. 6, both
  still "Lydian") — a finer distinction this app's family-level scope
  already declines to make (see Stage 2 above). It is not documented
  as a signal that moves a piece to a *different* family, which is all
  this app's classification claims.
- Checked this corpus's own data directly rather than assuming
  uniformity: Palestrina's own soprano clef is `*clefG2` in 1,316 of
  1,318 pieces (99.8%) regardless of flats or finalis — cleffing is
  effectively constant here, so there is no second signal this
  mapping could read even if it tried to.
- **Caveat, disclosed rather than hidden:** that near-total uniformity
  could be a real fact about this specific repertoire, or could be an
  artifact of the Humdrum encoding normalizing clefs for legibility
  rather than preserving whatever the original print/manuscript used —
  not independently checked against a facsimile or original edition.
  This clef-uniformity check was Palestrina-specific and was **not**
  re-verified for the other 6 collections when the transposition logic
  was extended corpus-wide.

**~1.1% of the corpus (49 pieces) carries 2, 3, or 4 flats**, not just
0 or 1, and is deliberately left unreclassified — genuinely rarer, and
the further transposition that many flats would imply (a second
transposition up another fourth, by analogy) was not independently
verified before shipping this, so it's left as `nonstandard` rather
than guessed. No sharp-signature piece was found anywhere in the
corpus.

## Corpus-wide implementation and verification

Palestrina's own flat count could be read for free from a local
Humdrum `*k[...]` header line. Extending this to the other 6
collections needed one raw-content fetch per piece, per format —
[`scripts/augment_key_signatures.py`](scripts/augment_key_signatures.py):

- **Humdrum kern** (`jrp`, `1520s`, `tasso`, `seils`, `lassus_psalms`,
  Palestrina): `*k[...]` line — flat count = number of `-` characters
  (or negative, for `#`, though none were found).
- **MEI** (`crim`): `key.sig="Nf"` attribute (or `"Ns"` for sharps) on
  each `<staffDef>` — confirmed by direct inspection of a real fetched
  MEI file (no `<keySig>` element; it's an attribute) before writing
  the parser.
- **MusicXML** (Monteverdi's `.mxl` files, `music21` collection):
  `<fifths>` element inside the zip (sign-flipped from MusicXML's own
  circle-of-fifths convention: `flats = -fifths`), with UTF-16 BOM
  detection for the ~76% of these files that need it.

Run once as a resumable background batch job: **4,252 pieces fetched,
0 errors.** `data/finalis.jsonl` now carries a `flats` field on every
record with a real finalis (0=none, N=N flats, verified distribution:
0 → 2,389; 1 → 1,829; 2 → 47; 3 → 1; 4 → 1 — no sharps anywhere).

### Reclassification impact, verified directly against the data

**Corpus-wide: 1,753 of 4,267 pieces with a determined finalis and
flat-count (41.1%)** get reclassified into a different modal family by
this fix, relative to reading the bare finalis alone. Per collection:

| Collection | Reclassified | Total | % |
|---|---|---|---|
| `1520s` | 340 | 667 | 51.0% |
| `crim` | 143 | 309 | 46.3% |
| `tasso` | 231 | 503 | 45.9% |
| `seils` | 13 | 30 | 43.3% |
| `lassus_psalms` | 20 | 50 | 40.0% |
| `jrp` | 521 | 1,338 | 38.9% |
| `music21` (Palestrina + Monteverdi) | 485 | 1,370 | 35.4% |
| **Total** | **1,753** | **4,267** | **41.1%** |

This is not a rare edge case in any of the 7 collections — it is
close to *half* the corpus in every one of them. Palestrina alone: 459
of 1,318 pieces (34.8%), of which the F-final/mollis→Ionian case (the
one that originally prompted this investigation) is 184 pieces on its
own.

**End-to-end functional verification:** the actual Browse-tab filter
logic (`_modal_family_key(record['finalis'], record['flats'])`) was
run against the real data for all 7 collections, confirming 644
correct Ionian-family matches spanning every collection and correctly
mixing untransposed C-final pieces with transposed F-final-plus-flat
pieces — not just checked in isolation on a handful of hand-picked
cases.

## Known, disclosed limitations

- **49 pieces (2–4 flat signatures)** are left as `nonstandard` rather
  than force-transposed by an unverified rule.
- **Cleffing** (Powers' third "tonal type" marker) is not used at all;
  checked and found not to matter for *family-level* classification in
  Palestrina specifically (see above), but that check was not repeated
  for the other 6 collections.
- **No ambitus/range analysis** anywhere in this pipeline — authentic
  vs. plagal is never disambiguated, by design (see "What 'modal
  family' actually claims" above).
- **Never tune a parameter to match a known-correct result:** none of
  the numbers above were fit to a hand-verified answer after the fact
  — the confidence-tier design was validated on hand-checked samples
  *before* being applied to the whole corpus, and the transposition
  mapping came from Powers/Hermelink's independent classification of
  Palestrina, not from adjusting anything until it matched.

## Bibliography

- Heinrich Glarean, *Dodecachordon* (Basel, 1547) — the primary source
  for the 12-mode system this app's 6 families are drawn from.
- Harold S. Powers,
  ["Tonal Types and Modal Categories in Renaissance Polyphony,"](https://doi.org/10.1525/jams.1981.34.3.03a00030)
  *Journal of the American Musicological Society* 34, no. 3 (1981):
  428–470. DOI `10.1525/jams.1981.34.3.03a00030`, verified via
  Crossref.
- Siegfried Hermelink, *Dispositiones modorum: Die Tonarten in der
  Musik Palestrinas — und seiner Zeitgenossen* (Tutzing: Hans Schneider,
  1960) — as cited in Powers 1981; not independently read for this
  project.
- Bernhard Meier, *Die Tonarten der klassischen Vokalpolyphonie*
  (Utrecht: A. Oosthoek, 1974); trans. Ellen S. Beebe, rev. by the
  author, as *[The Modes of Classical Vocal Polyphony: Described
  According to the
  Sources](https://openlibrary.org/works/OL3121670W/The_modes_of_classical_vocal_polyphony)*
  (New York: Broude Brothers, 1988) — source for the CVF
  (Cantizans/Tenorizans/Bassizans/Altizans) cadence-voice terminology
  the finalis signals (`Low`/`Tone`) are read from.
- Daniel C. Tompkins,
  ["A Cluster Analysis for Mode Identification in Early Music
  Genres,"](https://link.springer.com/chapter/10.1007/978-3-319-71827-9_24)
  in *Mathematics and Computation in Music* (MCM 2017), Lecture Notes
  in Computer Science vol. 10527 (Cham: Springer, 2017) — ran on this
  same music21-bundled Palestrina corpus; source for both the
  family-not-specific-mode scope decision and the Tritus/F-final
  caveat.
- Alexander Morgan, Daniel Russo-Batterham, and Richard Freedman,
  ["Musicologists and Data Scientists Pull out all the Stops: Defining
  Renaissance Cadences
  Systematically,"](https://www.academia.edu/109443988/Musicologists_and_Data_Scientists_Pull_out_all_the_Stops_Defining_Renaissance_Cadences_Systematically)
  Music Encoding Conference, Halifax, Canada, 2022 — the cadence
  detection method the `Low`/`Tone` finalis signals depend on.

## Where the underlying code lives

- [`scripts/precompute_finalis.py`](scripts/precompute_finalis.py) —
  `compute_finalis()`, the 3-signal cross-check and confidence tiers.
- [`scripts/augment_key_signatures.py`](scripts/augment_key_signatures.py) —
  per-collection key-signature (`flats`) extraction.
- [`app.py`](app.py) — `_MODAL_FAMILY_BY_FINAL`, `_MOLLIS_TRANSPOSITION`,
  `_modal_family_key()`, `_TRITUS_CAVEAT`, and the Browse-tab "Modal
  family" filter UI itself.
- [`data/finalis.jsonl`](data/finalis.jsonl) — one record per piece:
  `label`, `finalis`, `source` (confidence tier), `flats`, `detail`.
- `finalis_findings.md` (this repo) — the full dated investigation log
  this document distills; read that for the individual bug traces
  (evaded Bassizans, desynced Monteverdi parts, the 24-piece validation
  sample) in their original chronological form.
