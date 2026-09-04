# Renaissance Polyphony Research Toolkit

This app works with **symbolic notation** of Renaissance polyphony —
MusicXML, Humdrum `**kern`, MEI, not audio or MIDI — the representation
[CRIM Intervals](https://github.com/HCDigitalScholarship/intervals) needs
for its structural analyses. Encoded this way, the repertoire is
scattered across several separate archives online; this app gathers
**~4,300 such pieces from 7 of them** into one searchable, analysis-ready
place. Run cadences (e.g. `Authentic → G`), points of imitation, and
homorhythmic passages on any piece, see where they fall across its
structure, and take the results further: an annotated score marked
**directly onto the notation itself** (ready for MuseScore, Finale, or
any notation software), a PDF to read or print, a raw score for your own
code, or a CSV dataset across a whole search.

CRIM Intervals (Morgan & Freedman, CRIM Project) analyzes contrapuntal
voice functions (Cantizans, Tenorizans, Bassizans, Altizans, etc.) rather
than just harmonic labeling — built for exactly this repertoire. This
tool adds what CRIM's own output doesn't give you on its own: the
results written back into a real, readable score file, plotted across
the piece, or exported as data ready for your own analysis.

Started as a single-purpose "Cadence Annotator," then "Renaissance Score
Workbench" once the collection-aggregation side grew into something
worth naming on its own; renamed again once cadence annotation stopped
being the single headline feature among several. Built as a side tool
for a physics thesis on network-science analysis of
Palestrina's polyphony — the app itself has no dependency on that project.

**[Open the live app](https://renaissance-polyphony-research-toolkit.streamlit.app/)**
— no install needed, just a browser. First load after a quiet spell can
take 30-60s while it wakes up (Streamlit Community Cloud's free tier
sleeps idle apps); after that it's fast. `.github/workflows/keep_awake.yml`
pings the app every 6 hours specifically to keep this from happening in
the first place — a scheduled `curl`, no external service needed, free
to run indefinitely on a public repo.

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://renaissance-polyphony-research-toolkit.streamlit.app/)

## Use it

**🔍 Browse all** — search all ~4,300 pieces across every collection at
once by composer or title. Pick a match, hit **Preview** to see
its voice count and whether it has encoded text/lyrics before committing
to anything heavier, or go straight to the button next to it — labeled
**Analyze** when at least one analysis checkbox below is checked (it
runs the checked analyses and shows the results before the file), or
plain **Download** when none are (nothing runs, you just get the piece
back).

Every search gets a CSV manifest of *every* match (not just the 50 shown
in the picker below), always available:
**"📄 Download all N match(es) as CSV"** — collection, composer, the
display label, and a source URL or `music21` corpus path for each,
meant to be loaded with `pandas` and fetched/parsed directly in your own
script, no dependency on this app at all.

Everything heavier lives inside one **"📦 Bulk downloads"** expander,
kept collapsed by default since most searches just want the CSV above.
Inside, one set of checkboxes (Cadences/Points of imitation/Homorhythm —
the same three the single-piece Analyze uses) governs all three exports
below it at once: leave everything unchecked for plain, unmodified
files, or check any of the three to get annotated ones — either version
is one click away without re-checking anything. Each export has its own
match cap, actually benchmarked (not guessed) against a real 25-piece
mixed sample (20 music21-bundled + 5 JRP), each sized to keep the
worst-case wait around 2.5-3 minutes rather than sharing one number that
would either be painfully slow for the most expensive export or
needlessly conservative for the cheapest one:
- **MusicXML** (cap: 25) — fetches, parses, and converts every match to
  a real MusicXML file (annotated with the checked analyses, or plain if
  none are checked) and zips them. ~7.5s/piece measured for the
  annotated case (the button's own worst case, since it now covers both
  modes).
- **PDF** (cap: 8) — renders each match to PDF via the same
  [Verovio](https://www.verovio.org/) path the single-piece "Download
  PDF" button uses (annotated or plain, same rule as MusicXML above),
  then zips them together. By far the most expensive of the three per
  piece — ~20s/piece measured (real analysis *and* a real render) — so
  it gets the tightest cap, not the same one as the cheaper exports.
- **Analysis data (CSV)** (cap: 40) — the one export that genuinely
  needs at least one analysis checked (there's no "plain" version of
  analysis data): runs the checked analyses across every match and hands
  back CRIM's own raw columns (`CadType`/`Tone`/`RelTone` for cadences,
  `Presentation_Type`/`Soggetti`/`Voices` for points of imitation,
  `hr_voices` for homorhythm), one CSV per analysis, each row tagged
  with `collection`/`composer`/`label` so pieces stay identifiable once
  every match is concatenated together — ready for your own stats, not
  just a count of what was found. Also builds a **per-piece density
  comparison CSV** — one row per piece, one column per checked analysis,
  the same density figure (fraction of measures touched — see below) a
  single-piece Analyze shows, so several pieces can be compared side by
  side directly, which the per-event CSVs above don't make easy on their
  own. A blank cell means that analysis wasn't computed for that piece
  at all (never requested, or it failed); a `0` means it genuinely found
  nothing — kept distinct rather than collapsed into one blank. Cheapest
  of the three per piece (~4s, no render), so it gets the highest cap.

These numbers come from one real benchmark run on one machine, not an
exhaustive test across many pieces or the actual deployed environment --
a solid starting point, not a guarantee. Any of these that fails to
fetch/analyze/render for one particular piece is skipped and reported by
name, not silently dropped from the count.

Handy for "give me every Josquin piece across all 7 collections to
analyze myself" — search "Josquin", download the CSV manifest above, or
open the bulk downloads expander for the ZIP (plain or annotated), the
analysis-data CSVs, or the PDFs.

No search at all needed for the **whole corpus's metadata** either (not
the scores themselves): a **"📦 Download the whole corpus metadata"**
expander sits right below the search box, with a CSV manifest (all
~4,300 pieces, all 7 collections at once, same columns as a search
result's own CSV export), a small per-collection piece-count CSV, and a
per-collection *composer*-count CSV (how many pieces each composer has
*within* their own collection — not merged across collections, since
composer spelling isn't consistent between collections).

At the bottom of this tab, below the search results, a composer word
cloud across all ~4,300 pieces, all 7 collections merged, sized by
piece count — no search or click needed, it renders as soon as the tab
loads. Unlike the composer-count CSV above, this merges composers
across collections for a visual overview rather than a precise count,
and folds known same-composer name variants together first: JRP's (and
some CRIM entries') "Lastname, Firstname" order is flipped to match
everyone else's, and a short, manually verified alias list catches the
rest the flip can't — e.g. CRIM's "Giovanni Pierluigi da Palestrina"
merging into music21's "Palestrina" (1318 + 52 pieces, previously shown
as two separate words), or a capitalization difference in "Josquin
Des/des Prez". A rarer, unverified variant can still show up as two
words — a wrong merge would be worse than that, so only checked cases
went in (see `_normalize_composer_for_wordcloud`'s docstring for what
was found and deliberately left out).

Or pick a piece from one collection directly. Every tab below filters by
**Composer**, one at a time with an "All composers" default (each
collection has its own clean, internally-consistent composer data —
that's *not* CRIM-exclusive, unlike genre below — the filter is just
absent wherever a collection genuinely has only one composer, e.g.
Lassus's Geistliche Psalmen):
- **music21 corpus** — Palestrina (~1300 pieces) or Monteverdi, picked by
  composer then a real title (e.g. "Missa Ad coenam Agni: Agnus I"), not
  an internal file id
- **CRIM Project corpus** — 359 pieces: Lassus's parody masses plus the
  motets/chansons/madrigals they're modeled on, live from crimproject.org.
  Also filterable by **genre** (Motet/Madrigal/Mass movement/Chanson/...)
  — the only one of the 7 collections where genre exists as real
  per-piece structured data at all, which is why that particular filter
  lives here and nowhere else (not on Browse, not on any other tab).
- **Josquin Research Project** — ~1340 pieces, 21 Franco-Flemish
  composers (Josquin, Ockeghem, Obrecht, la Rue, and more), live from
  their public GitHub repository
- **1520s Project** — 662 pieces, ca. 1510-1540, mostly France/Germany/
  Italy/the Low Countries, live from their public GitHub repository
- **Tasso in Music Project** — 503 madrigal settings of Torquato Tasso's
  poetry (1570s-1640s, many composers), live from their public GitHub
  repository
- **More collections** — SEILS (Italian secular songs, ca. 1600) and
  Lassus's Geistliche Psalmen, two smaller collections sharing one tab

Or use **📤 Upload your own file**, right next to Browse, to bring your
own score: MusicXML (`.xml`/`.musicxml`) or MEI (`.mei`).

Every collection has three independent checkboxes before hitting
**Analyze**/**Download** (see above for which label you'll actually see):
- **"Annotate cadences"** — CRIM's `cadences()`, marked in red. On by
  default, since it's this app's original and still-primary feature.
- **"Mark points of imitation"** — CRIM's `presentationTypes()`,
  which finds where a melodic subject enters in one voice and is
  imitated by others (a Point of Entry, Imitative Duo, or Fuga), marked
  in blue. Off by default.
- **"Mark homorhythmic passages"** — CRIM's `homorhythm()`, which
  finds passages where two or more voices move together in the same
  rhythm while singing the same words (a chordal, declamatory texture),
  marked in green. Off by default.

Uncheck all three and hit Download anyway to just get the piece back
unmodified — useful if all you want is this app's aggregated access to
a piece (in real MusicXML, converted from whatever format its home
collection actually ships) without any CRIM analysis at all. The
download button and file name are honest about which case you're in
("Download annotated MusicXML" vs. plain "Download MusicXML"). The
downloaded file's name itself (single-piece downloads and every bulk
ZIP) isn't just the piece's own machine ID (e.g. `Gloria_42.xml`) --
`_rich_filename_stem()` combines that ID with the piece's full
human-readable label (composer, mass/collection title, movement --
everything Browse's own label already carries), e.g. `Missa Quem
dicunt homines - Gloria (Gloria_42)_annotated.xml`, so a bulk ZIP's own
file listing identifies every piece on its own, without needing to
cross-reference back to the search that produced it. Pure string
work -- no extra computation or network call, so no meaningful cost.

A **"Build annotated PDF"** button sits right below the MusicXML
download (same "annotated" vs. plain wording, no leading icon -- kept
coherent with the MusicXML button above it), rendered via
[Verovio](https://www.verovio.org/) — the same MEI/MusicXML engraving
library CRIM Intervals itself already uses for its own Jupyter helpers,
so this isn't a new dependency to the project. It reads the exact same
MusicXML the download above offers, so cadence colors and text labels
come through correctly (an earlier version went through music21's own
LilyPond backend instead, which re-derived its own notation from scratch
and had real gaps — every cadential note came out the same red regardless
of which analysis colored it, and the text labels were dropped entirely;
not a page-layout quirk, an actual translation bug). Clicking it renders
the PDF on that click and swaps in a second, real download button once
it's ready — cached per piece for the rest of the session, so switching
back to a piece you already built one for doesn't re-render it.

Whenever at least one analysis actually finds something, a small strip
plot shows where each event falls across the piece (0 = start, 1 =
end) — one lane per analysis, in the same colors as the score (red
cadences, blue points of imitation, green homorhythm) — so you can see
a piece's structural shape at a glance without opening notation
software at all.

Whenever at least one analysis was requested, a **"📋 Methods-section
description"** expander appears alongside the result — a ready-to-copy
paragraph (with `music21`/CRIM citations already in it) describing
exactly which analyses were run, for pasting straight into a paper's
Methods section. It only mentions the ones actually checked, so it never
overclaims what a given download represents.

In-app expanders explain what the cadence labels mean and how the
detector actually works, in case any of it isn't self-explanatory at a
glance.

## Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens at `http://localhost:8501` — **only while this command is actively
running in your own terminal**; it's not a public address, so it's for
testing before you deploy, not for sharing with anyone else. The link
above is the one to share.

## How it works, briefly

1. The score is parsed with [music21](https://www.music21.org/).
2. If **"Annotate cadences"** is checked (on by default), CRIM's
   `.cadences(voice_detail=True)` is run on it, which — beyond the
   usual cadence type/tone/measure columns — also returns a `PartMap`:
   which staff performed each cadential role (Cantizans, Tenorizans,
   Bassizans, ...) at each cadence. Unchecking it (along with the other
   two checkboxes) skips CRIM entirely and hands back the parsed score
   as-is.
3. `annotate_cadences.py` uses `PartMap` to find the exact notes involved
   and colors them, and inserts a text label into the score's measure
   structure at the cadence's precise beat. Measure lookups are indexed
   once per part rather than re-scanned per cadence — a real bottleneck
   on larger pieces before that fix (benchmarked: 13.2s → 2.3s on a
   123-measure/6-voice piece). A measure's `TimeSignature` (needed to
   convert "beat" into an exact insertion offset) is looked up via
   context rather than assumed local to that measure — re-exporting a
   score to MusicXML and re-importing it (e.g. downloading a piece from
   this app and re-uploading it) can leave that context unresolvable for
   a specific measure even when the original parse had it; when that
   happens the label is skipped and counted as "missed" instead of
   crashing the whole annotation. Verovio (like most simple score
   renderers) doesn't auto-avoid collisions between text annotations, so
   `_LabelLane` (`annotate_cadences.py`) staggers a label's vertical
   position off its category's base row whenever the previous label of
   the SAME category (cadence, points-of-imitation, or homorhythm) landed
   within 6 quarter notes of it — a real, confirmed need, not a
   hypothetical: Palestrina's *Missa Quem dicunt homines: Gloria*
   (`Gloria_42`) has a run of cadences 4-6 quarter notes apart across
   measures 148-150 that rendered as an unreadable pile of text before
   this fix, checking every checkbox at once made it much more likely to
   hit (more categories' labels competing for the same space), not the
   cause on its own.
4. **Browse all** reuses every collection's own piece list (built once,
   cached) plus a lightweight per-piece check (already-available metadata
   for CRIM/music21; a single small-file fetch for the five kern-backed
   collections) to preview voice count and text/lyric encoding without
   running the full pipeline first. Every collection's piece list is
   re-fetched live from its source at most once an hour (`@st.cache_data
   (ttl=3600)`) -- if e.g. the Josquin Research Project adds pieces to
   their repo, this app picks them up on its own within the hour, no
   code change or manual refresh needed.
5. The **points-of-imitation checkbox** runs CRIM's `presentationTypes()`
   and marks each entry note in blue, labeling the first entry of each
   instance with its type (PEN/ID/FUGA). Voice names in that output
   (e.g. `Part-2`) are CRIM's own disambiguated part-naming convention,
   not a fixed staff-position number like cadences use — reused directly
   from `ImportedPiece._getPartNames()` rather than reimplemented.
6. The **homorhythm checkbox** runs CRIM's `homorhythm()` and colors
   every note of every matching passage in green, labeling the start of
   each one. `homorhythm()`'s raw output isn't pre-consolidated into
   distinct passages the way `presentationTypes()` is — consecutive rows
   can describe the same passage via overlapping sliding windows — so
   this tool deduplicates them into one label per passage before writing
   anything to the score.
7. The **strip plot** places each event by its actual measure number,
   not CRIM's 0-1 `Progress` fraction — a number that reads directly off
   the score. Cadences expose `Measure` as a plain column and
   `homorhythm()` as an index level; `presentationTypes()` exposes
   neither, so there the first voice's own entry measure is parsed out
   of its `Measures_Beats` field instead (the same value this tool
   itself tries first when placing that instance's label on the score).
8. Right above that plot, a **density** stat tile per analysis —
   the fraction of the piece's measures that contain at least one
   detected event of that type (e.g. "Cadence density: 12%"). Deliberately
   the *fraction of measures touched*, not a raw event count, so a
   16-measure piece and a 160-measure piece are directly comparable, and
   so a homorhythm passage spanning several measures (crim_intervals'
   own `homorhythm()` returns overlapping sliding-window rows per real
   passage, not one row per passage — see `annotate_homorhythm`'s own
   docstring) doesn't inflate the count the way a plain `len(events)`
   would.
9. Before any of that, whether or not an analysis was even requested,
   a **source edition** panel shows whatever this specific piece's own
   file actually says about where its encoding comes from — which
   matters a lot for Renaissance music, where the same "piece" can be
   edited very differently by different scholars. Not one blanket claim
   for the whole app: checked directly against real files first, and
   different collections encode genuinely different things. music21's
   bundled Palestrina files (and every other Humdrum-`**kern` collection
   here — JRP, 1520s, Tasso, SEILS, Lassus Psalms) carry `!!!YOR`/`!!!YOO`
   (the original **print** edition and its publisher, e.g. *"Le Opere
   Complete, v. 18, p. 126"*) or `!!!SCA` (the modern **critical**
   edition name, e.g. *"New Josquin Edition 3.1"* for JRP) — read
   generically off whichever of these `humdrum:XXX` reference-record
   fields music21's own parser exposes, since different pieces populate
   different subsets. CRIM's MEI files carry something richer still —
   the actual editors' names and the original print source (publisher,
   date, physical repository) — extracted directly from the MEI's own
   `<respStmt>`/`<manifestation>` elements (one small extra fetch of
   that same MEI file, since none of this reliably survives music21's
   own, comparatively thin, MEI import into its Metadata object).
10. A few small, deliberately restrained visual touches. Every spinner
    that's actually running CRIM analysis (Preview's quick check, the
    single-piece Analyze/Download button, the Upload tab, the bulk
    analysis-data export) shows a random line from a short, hand-picked
    set of real music-theory terms — "Untangling counterpoint...",
    "Splitting the comma..." (meantone temperament's actual mechanism,
    not a generic "tuning" reference), "Coloring the breves...", etc. —
    instead of generic "loading..." text. Spinners where nothing is
    actually being analyzed yet (Browse's corpus index loading, the
    bulk ZIP export) deliberately draw from a *separate*,
    fetching/cataloguing-themed pool instead ("Gathering the
    partbooks...", "Cataloguing the collections...") rather than reuse
    the analysis lines — the ZIP export in particular explicitly
    documents that it runs no analysis at all, so a counterpoint joke
    there would undercut that real clarity, not just be an off-theme
    whimsy. A one-time note-shape flourish marks the boundary between
    the intro/Credits section and the tabs (placed *once*, not
    scattered — a decorative element repeated throughout a page reads
    as clutter fast, which is also why a "featured piece" widget idea
    raised alongside these was dropped); every button shows a small
    custos (the manuscript guide-mark) on hover, via a pure-CSS
    `::after` pseudo-element; and the Preview/Analyze/Download/every
    download button use Streamlit's own `type="primary"` styling, which
    pulls its color directly from `.streamlit/config.toml`.

## Finalis precompute and the Modal family filter

*(This section's own history below, up through "Finalis filter UI has
been removed", is kept as-written — real bugs, found and fixed in that
order. The filter it describes removing is back, redesigned; see the
update at the end of this section.)*

A **Finalis** filter for Browse is planned — full-corpus, not scoped to
one search — but that needs a Finalis *value* for all ~4,300 pieces
already sitting somewhere cheap to read, since computing it live would
mean running real CRIM cadence detection on every piece just to
populate a filter dropdown. First test run (5 music21 pieces) came back
clean, including a real check against a hand-verified answer: two of
the five pieces were *Missa De Beata Marie Virginis (II)/(III)*'s own
Agnus movements, both correctly returning **G** — matching this
project's own independently hand-verified finalis for exactly those
two pieces. The full corpus run is still pending:

- `corpus_sources.py` — the piece-*listing* logic (which pieces exist,
  across all 7 collections) extracted out of `app.py` into its own
  module, so a script outside the Streamlit app can enumerate the exact
  same pieces without a second, drifting copy of that logic. `app.py`
  imports from it now instead of defining these functions itself.
- `scripts/precompute_finalis.py` — a standalone batch script. For each
  piece: last detected cadence's `Low` column (the *clausula basizans*
  convention — matching how this corpus's one hand-verified finalis,
  Agnus_00 = G, was actually confirmed), falling back to
  crim_intervals' own cruder `.final()` (literally the lowest note at
  the very last moment, no cadence detection) only if a piece has zero
  detected cadences. Writes one JSON record per piece to
  `data/finalis.jsonl`, keyed by the piece's own Browse label. Designed
  to be resumable across many separate runs (reads what's already
  there, skips it) and to commit its own progress periodically, not
  just at the end.
- `.github/workflows/precompute_finalis.yml` — triggered manually
  (`workflow_dispatch`, from the Actions tab) since a full run is a
  genuinely multi-hour job, almost certainly longer than one workflow
  run's own time limit. An unrestricted run (blank `limit`, blank
  `collection` — the "whole remaining corpus" mode) self-chains: if the
  corpus still isn't fully recorded by the time its own run ends, it
  automatically queues another run of itself, so working through the
  full corpus doesn't need someone to notice each run stopped and
  re-trigger it by hand. A deliberately scoped run (`limit` and/or
  `collection` given) never self-chains — those stay one-off.

The full corpus (4,314 pieces across all 7 collections) finished
precomputing on 2026-08-30 — `data/finalis.jsonl` has one record per
piece (4,262 with a real pitch class; the other 52 are recorded as
`finalis: null` rather than a silent guess — precompute genuinely
couldn't determine one for those).

**Known data-quality issue, found 2026-08-30 by a user spot-checking a
result:** Monteverdi's "O Mirtillo, Mirtill' Anima Mia" was recorded as
finalis E, but the piece actually ends on D. Root cause traced directly
(not guessed): this specific bundled music21 corpus file has
desynchronized voice-parts — Canto and Continuo's own encoded content
runs out at offset 276 (quarter notes) and Quinto's at 288, while the
piece's real ending (confirmed by ear, and matching `piece.final()`) is
at offset 340. For the whole final ~16 measures, several voices simply
aren't present in the data at all — not resting, their streams have no
further content, notes or rests. Both the cadence detector (which needs
a real complementary voice-pair to recognize a cadential pattern) and
`piece.final()` end up reading a texture silently missing most of its
real voices. Sampling 6 Monteverdi pieces found this in 2 of them, so
it's a real, recurring defect in a meaningful slice of the corpus, not
a one-off. Fixed by adding a cheap check (`_parts_desynced` in
`scripts/precompute_finalis.py`): when a piece's own parts don't all
reach the same total duration (tolerance: one measure, 4 quarter
notes, to allow a genuine pickup-measure difference), its finalis is
still recorded as a best guess but tagged `source: "part_duration_mismatch"`
instead of `"cadence"`/`"final_fallback"` — flagged low-confidence
rather than presented at face value. The full corpus recompute with
this fix ran and finished the same day (2026-08-30) — `data/finalis.jsonl`
now has a fresh record per piece, including any newly-flagged
`part_duration_mismatch` entries.

**Second known issue, found the same day, same way (a user spot-checking
a real filtered result):** Palestrina's *Missa Quem dicunt homines: Gloria*
(`Gloria_42`) was recorded as finalis E, but the piece actually ends on
C — `piece.final()` agrees (`'C3'`), and this piece's voice-parts are
NOT desynced (`_parts_desynced` correctly returns False here; all 4
parts reach the identical offset 1208). Root cause, traced directly:
the piece's very LAST cadence (m.150, "Clausula Vera", CVFs `CTu`) has
its Cantizans and Tenorizans fully realized (uppercase C, T) resolving
to Tone **C** — but its Bassizans is *evaded* (lowercase `u`: "goes down
a third instead" of the expected leap), so the bass actually sounds E
at that exact moment, not C. `cadences()`'s own `Low` column reports
whatever the bass literally did (E), while its `Tone` column (the
Cantizans's own goal note, unaffected by what the bass does) correctly
reports C. Reading `Low` alone — this precompute script's whole design
so far — is blind to exactly this case: a single evaded CVF role
corrupting the bass reading even when the cadence's overall `CadType`
looks fully resolved (not prefixed `Evaded`/`Abandoned` at all, since
that prefix reflects the cadence as a whole, not each individual voice).

Given two independent, real failure modes found by simple spot-checks
in one afternoon, the **Finalis filter UI has been removed** from
Browse and all six collection tabs (the underlying precompute script,
data file, and desync check all stay in the repo — this is a UI-only
rollback) until there's a heuristic that doesn't need after-the-fact patching
every time someone happens to check a result by ear. A more reliable
approach would need to consider, at minimum: preferring `Tone` over
`Low` when the Bassizans CVF is lowercase, and cross-checking `Tone`
against `piece.final()` the same way `_parts_desynced` already
cross-checks part durations.

**Update — redesigned, back in the UI:** `compute_finalis()` (in
`scripts/precompute_finalis.py`) now does exactly the cross-check the
paragraph above called for, and more: it computes `Low`, `Tone`, and
`piece.final()` independently, requires them to actually agree (not
just picks one), and tags every result with a confidence tier
(`confident_unanimous`/`confident_majority`/`low_confidence_split`/
`single_signal`/`part_duration_mismatch`/`error`) rather than a single
undifferentiated guess. Validated against 39 hand-checked pieces across
two independent random samples (100% on the confident tiers) before the
full corpus was recomputed — `data/finalis.jsonl` now has one record
per piece, 4,319/4,319. Browse's own **Modal family** filter (not a
Finalis pitch-class filter — deliberately coarser, see the code's own
comments on why) groups by which of the 4 traditional finals (Protus/
Deuterus/Tritus/Tetrardus) or Glarean's two added ones (Ionian/Aeolian)
a piece's finalis falls into, with an "only high-confidence results"
option using those same tiers.

**Cantus mollis transposition** (added after a user question: "if the
finalis is F and there's a flat in the clef, shouldn't that be
transposed Ionian, not Tritus?"): a flat in the key signature
transposes the WHOLE diatonic collection up a fourth, so a piece under
that signature sounds like the untransposed mode a fourth below its
own final — checked directly against Powers, "Tonal Types and Modal
Categories in Renaissance Polyphony" (*JAMS* 34/3, 1981), which cites
Hermelink's own etic study of Palestrina classifying a cantus-mollis
G-final Palestrina piece as "Hypodorian" (transposed up a fourth from
D) — the same reclassification this filter makes. On Palestrina alone,
this reclassifies 459 of 1318 pieces (35%) — not a rare edge case; F
alone was 184 of those, the exact case that prompted the question.
Deliberately checked, not assumed, whether dropping cleffing (Powers'
own "tonal type" is system + cleffing + final, three markers, not the
two — system + final — this filter uses) loses anything here: it
doesn't, because Palestrina's own soprano clef is the same (`G2`) in
1316 of 1318 pieces regardless of flats, so there's no second signal
to read; where cleffing *does* vary in Powers' own examples, its role
is authentic/plagal, a finer distinction this filter doesn't attempt
anyway (not independently re-checked for the other 6 collections).
`scripts/augment_key_signatures.py` extends the key-signature lookup
this needs to all 7 collections — Palestrina's own flat count was free
from a local file; the other 6 needed one raw-content fetch per piece
(Humdrum's `*k[...]` line for the 5 kern collections, MEI's
`key.sig="Nf"` attribute for CRIM), run once as a background batch job
(0 errors across 4,252 fetches) — so corpus-wide, not just Palestrina's
own share of it, this reclassifies 1,633 of 4,267 pieces with a real
finalis (38.3%). A further ~1.1% of the corpus (49 pieces) carries 2,
3, or 4 flats and is deliberately left unreclassified — genuinely
rarer, and the further transposition that many flats would imply

**Second cross-check, 2026-09-03, prompted by a user question** ("how
do I know an unmarked accidental isn't hiding a different mode?" for a
G-final/no-flat piece): checking that specific worry directly (on
Agnus_00: 45 F-naturals vs. only 10 F-sharps, all explicitly *notated*
at scattered internal-cadence points, not silently assumed diatonic —
this corpus's transcription does capture musica ficta as real
accidentals) surfaced something more useful than the original question:
95.4% of Palestrina's own Humdrum files (1,257/1,318) carry an embedded
editorial mode tag (`*X:dor/phr/lyd/mix/aeo/ion`) — an independent
judgment, not derived from this app's own finalis/cadence computation,
so agreement with it is real corroboration. Cross-checked against it,
1,018 confident-tier comparable pieces:

| Final + flats | This app says | Agreement |
|---|---|---|
| G + 0 | Tetrardus | 98.8% (240/243) |
| G + 1 | Protus | **100.0%** (183/183) |
| F + 1 | Ionian | **100.0%** (170/170) |
| D + 0 | Protus | 96.5% (111/115) |
| A + 0 | Aeolian | 91.2% (73/80) |
| E + 0 | Deuterus | 92.6% (87/94) |
| C + 0 | Ionian | 89.4% (76/85) |
| D + 1 | Aeolian | 71.8% (28/39) — real majority support, kept, disclosed as weaker than G/F |
| C + 1 | *was* Tetrardus | **0.0%** (0/9) — **retracted**, see below |
| A + 1 | Deuterus | untested — only 2 such pieces exist corpus-wide, neither in a comparable tier |

The G-final and F-final mollis cases — the two independently attested
in the primary literature (Hermelink, and the original bug report,
respectively) — are now *also* perfectly confirmed by this fully
independent source. The C-final mollis case had no literature
attestation of its own (it was pure transposition arithmetic) and is
flatly contradicted here: all 9 comparable pieces are tagged Ionian,
not Tetrardus. **Retracted** — `_MOLLIS_TRANSPOSITION` no longer
includes `'C'`, so a C-final piece with a flat now falls through to the
plain untransposed Ionian mapping, matching the evidence. Plausible
reason (not independently confirmed): G-Mixolydian's untransposed form
has a real, documented tritone problem against F that mollis fixes;
Ionian's untransposed form has no such problem, so a flat there may
just be a customary color choice, not a transposition marker. This
retraction is why the reclassification count above dropped from an
earlier 1,753/41.1% to 1,633/38.3% — 120 corpus-wide C-final/mollis
pieces are no longer reclassified.
wasn't independently verified before shipping this; no sharp-signature
piece was found anywhere in the corpus at all.

Also since redesigned: Palestrina's long Gloria/Credo/Sanctus settings
are often split across several encoded files (e.g. Sanctus_92_a/b/c =
"First Section"/"Pleni"/"Hosanna") — 262 of Palestrina's real movements,
839 of its 1318 Browse-index rows before this. Browse (and the corpus
tab) now show ONE row per movement, and analyzing one actually stitches
its parts into a single continuous score first (`corpus_sources.
merge_movement_parts`) — cadences/points-of-imitation/homorhythm are
found across the WHOLE movement, including passages spanning a former
file boundary, not per fragment. A voice that drops out mid-movement
for a reduced-voice passage (checked directly: 77% of a random sample
of split movements do this somewhere, not a rare case) is padded with
rests rather than naively concatenated by part position, which would
silently misalign voices for most of the corpus. The original section
boundaries are still marked on the score (as rehearsal-mark-style
labels, e.g. "Pleni") so they're not lost.

## Known Monteverdi encoding quirks (title, and a blank-page PDF bug)

Found 2026-09-03 from a real user report ("il pdf è uno schifo... c'è
tutto uno spazio bianco sulla prima pagina" / titles showing as generic
"Music21 Fragment" text instead of the piece's real name), traced
directly rather than guessed:

- **Missing title.** Several Monteverdi `.mxl` files (e.g.
  `madrigal.3.7` = "Se per estremo ardore") carry no title at all in
  their own embedded metadata — `score.metadata.title` comes back
  `None` from a direct parse — even though the real title IS derivable,
  from a same-stem sibling file in music21's own metadata bundle (here,
  `madrigal.3.7.rntxt`, which does carry it; this is exactly what
  Browse's own dropdown label already reads). Left alone, this piece's
  exported MusicXML/PDF has no real title and music21's own writer
  falls back to a generic placeholder ("Music21 Fragment") — the same
  failure class already fixed for CRIM pieces (`_annotate_crim_piece`
  in `app.py`). Fixed the same way: `corpus_sources.parse_music21_piece`
  now fills in a missing title from `list_pieces_for_composer`'s own
  (already-correct) label lookup before returning the score.
- **Blank space on page 1 of the PDF.** Some of this corpus's
  Monteverdi Finale/Dolet exports encode a SECOND stanza of poetry as
  free-floating `<direction>`/`<words>` text positioned by a
  `relative-x` horizontal-cursor offset, instead of as real per-note
  `<lyric>` content — a Finale-specific layout hack that doesn't
  survive translation into music21's object model as anything but a
  pile of `TextExpression` objects with no positional meaning left.
  Checked directly, not assumed: **14 of 49** Monteverdi `.mxl` files in
  this corpus do this (`madrigal.3.5/.6/.7/.9/.10/.11/.12/.16/.17/.19/
  .20`, `4.12`, `4.20`, `5.3`), always in the Canto part's very first
  measure, which otherwise has NO real notes at all — e.g.
  `madrigal.3.7`'s Canto measure 1 holds 36 `TextExpression` objects and
  one filler `Rest`. Verovio has no way to interpret `relative-x`
  positioning and stacks each one on its own row, reserving a huge
  blank vertical block on the page — confirmed directly against that
  same piece's own rendered SVG: the gap between the Canto and Quinto
  staff labels was ~15000 of the page's 24940 SVG units (roughly 60% of
  the whole page), collapsing to the normal, uniform ~1800-unit spacing
  once fixed. Fixed via a new `app.py` helper,
  `_strip_phantom_verse_text`, called only inside `score_to_pdf_bytes`
  on a **copy** of the score: strips `TextExpression` objects sitting in
  a measure with zero real (non-rest) notes of its own AND more than 5
  of them — a real title/tempo marking never produces that combination,
  so this can't accidentally delete a normal annotation. The original
  `score` object — what "Download MusicXML"/"Download annotated
  MusicXML" actually offers — is untouched, so the second stanza's text
  is never actually lost, only left out of the rendered PDF/preview,
  which can't lay it out correctly either way.

Both fixes verified directly against all 14 affected pieces (titles all
resolve correctly, phantom text stripped, no crashes) plus two
unaffected control pieces (a normal Palestrina piece and a Monteverdi
piece with no phantom-text measure), confirming neither fix touches
anything it shouldn't.

## Number of voices filter (Browse tab)

Added alongside the Modal family filter, same tab, same precomputed-
data pattern (a request live-filtering ~4,300 pieces one-by-one over
the network on every Streamlit rerun isn't viable, the same reason
`data/finalis.jsonl` exists). `scripts/precompute_voices.py` writes
`data/voices.jsonl`, one record per piece: free from the CRIM
piece-list catalog (`number_of_voices`, already there) or a local file
for the `music21` collection (Palestrina/Monteverdi, same `**kern`-
spine-counting/`<score-part >`-counting convention `preview_piece`
already uses live for a single piece), one raw-file fetch per piece for
the other 5 (Humdrum) collections. Ran once as a background batch job:
**3,638 pieces resolved successfully.** Distribution: overwhelmingly 3–6
voices (483/1,637/878/397), a handful of extremes at both ends (57
single-voice entries — mostly CRIM's own plainchant-model movements,
not polyphony — up to a real 24-voice Josquin motet, "Qui habitat in
adjutorio altissimi," and a documented 12-voice Brumel mass, "Missa Et
ecce terre motus" — both spot-checked against well-known musicological
facts about these specific pieces, not just trusted at face value).

One real data-quality catch made while building this: 74 CRIM pieces
(all Ludwig Daser/Victoria motets) carry a literal `0` in CRIM's own
`number_of_voices` catalog field — a genuine upstream gap, not real
0-voice pieces. Treated the same as any other undeterminable case
(excluded from the file, not silently stored as a wrong value) —
`voices_for_row`'s CRIM branch now does `... or None` specifically
because of this, with the 74 affected pieces named in its own code
comment.

Keyed differently from `finalis.jsonl` on purpose: by the **grouped**
Browse label (`group_browse_rows`' own output), not the raw per-file
one — a finalis is only meaningful read off a piece's true ending, so
the modal-family filter resolves a grouped Palestrina movement to its
*last* real member; a voice count should reflect the movement's overall
scoring, so this file takes the **max** across every real member
instead, matching what `preview_piece` already showed for the same
piece in a single-piece preview. One upside of the different keying:
looking this up needs no per-row label translation at filter time,
unlike the modal-family filter's own `_finalis_lookup_label`.

## Real voice/part names for the 5 GitHub-hosted kern collections

Found 2026-09-04, from a user looking at exactly the 24-voice Josquin
piece the section above mentions ("Qui habitat in adjutorio
altissimi") and asking why the PDF just says "Voice" 24 times over,
with no way to tell one staff from another.

Root-caused directly, not guessed: music21's Humdrum importer
implements two of Humdrum's three `*I`-prefixed instrument
conventions — `*IC<class>` and the short mnemonic `*I<code>` (e.g.
`*Ibass`, which is how Palestrina's own files name voices, and which
DOES come through correctly) — but not Humdrum's own "printed
instrument name" convention, `*I"<name>` (e.g. `*I"Bassus6`). Read
`humdrum/spineParser.py`'s own source to confirm: its generic,
order-has-to-be-last `*I` branch tries the exact same short-code
lookup on the quoted name too, fails, and the caller silently swallows
the failure into a no-op object — it never reaches `Part.partName` at
all. **Checked how widespread this actually is before treating it as
one piece's problem**: sampled 10 real pieces from each of the 5
GitHub-hosted kern collections this app reads — `jrp`/`1520s`/`tasso`/
`lassus_psalms` use `*I"` for *every* sampled piece (10/10 each, so
this is most of that ~2,558-piece share of the corpus, not a rare
case); `seils` uses it for none (0/10) — presumably the short-code
convention Palestrina also uses, unaffected.

Fixed with a new `app.py` helper, `_fix_humdrum_quoted_part_names`,
called right after parsing in both places these 5 collections get
turned into a `Score` (`_annotate_kern_from_url`, used by every
per-collection tab's PDF/preview and by Browse's own bulk downloads;
`_import_piece_by_collection`, used by the raw analysis-table export).
Reads `*I"`/`*I'` (full name / abbreviation) tokens straight from the
raw kern text — a cheap header-only scan, same convention already used
elsewhere in this project — and matches them to the right `Part` via
each part's own `.id` (e.g. `spine_23`), which reliably carries its
0-indexed column position from the file's own `**kern` declaration
line — checked directly, and deliberately NOT assumed to match
`score.parts`' own iteration order, which turns out to be the *reverse*
of the file's left-to-right column order.

Verified end-to-end on the real reported piece: all 24 parts now read
`Superius`/`Superius2`.../`Bassus6` (matching the file's own explicit
`Four 6-ex-1 canons at the unison` structural annotation — see below),
not `Voice` × 24; a real PDF re-rendered cleanly (1.5MB, 0 errors).
Two regression checks confirmed nothing else moved: a Palestrina piece
(`*I<code>`-only, no `*I"` at all) came out byte-for-byte identical to
before; a `seils` piece would fall through this function's own
`if not names_by_col: return score` guard unchanged (not directly
re-tested, since no `*I"` tokens exist anywhere in that collection to
begin with).

**What "24 voices" for a single motet actually means** (the
musicological half of the same question): NOT 24 independently
composed melodic lines, and not 24 spatially separated choirs in the
later Venetian *cori spezzati* sense. The piece's own header literally
states its structure — `!!LO:TX:Z=20:t=Four 6-ex-1 canons at the
unison` — confirmed directly against the actual encoded part names
(`Superius`/`Altus`/`Tenor`/`Bassus`, each split into 6 numbered
copies, `Bassus`…`Bassus6` etc.): **4 real composed voices** (the
standard SATB layout), each one independently sung, in canon, by *6*
singers simultaneously — every copy singing the exact same melody,
entering one after another at staggered time offsets, all at the same
pitch level (a canon "at the unison," not transposed) and the same
notated mensuration (checked: every one of the 24 columns shares the
identical `*met(C|)` token, ruling out a proportion/mensuration canon
specifically — this is a plain time-delay canon, a "round," done 4
times over). 4 × 6 = 24 sounding parts from 4 actually-composed lines —
one of Josquin's most celebrated demonstrations of pure contrapuntal
technique (cited via the New Josquin Edition, `NJE 18.8`, this file's
own source).

## Credits & licensing

*(The same content is also in the app itself, in the "ℹ️ Credits & data
sources" expander at the top — most people using the deployed app will
never open this README, so attribution lives in both places.)*

**None of the structural analysis is this app's own work.** Every
cadence, point of imitation, and homorhythmic passage this tool marks
comes from calling [CRIM Intervals](https://github.com/HCDigitalScholarship/intervals)'s
own `cadences()`, `presentationTypes()`, and `homorhythm()` methods
directly — this app's contribution is aggregating the 7 sources below,
and writing CRIM's results back onto real notation, plotting them, and
exporting them as data. CRIM Intervals was built by Richard Freedman
(Haverford College) and the CRIM Project (crimproject.org) team, and is
licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
This app is an independent, unaffiliated project built on top of that
library — it isn't CRIM's own official web app (that's
[crimintervals.streamlit.app](https://crimintervals.streamlit.app/), a
separate tool by the CRIM team themselves). Scores are parsed with
[music21](https://www.music21.org/) (Cuthbert & Ariza, MIT;
[BSD-3-Clause](https://github.com/cuthbertLab/music21/blob/master/LICENSE)).

The 7 data sources, with the actual terms each one publishes (checked
directly against each repo's own license file, not assumed):
- **music21-bundled corpus** (Palestrina, Monteverdi) — ships inside
  music21 itself; see music21's own corpus documentation for terms.
- **[CRIM Project](https://crimproject.org/)** — see CRIM Intervals'
  license above; the same project publishes this corpus.
- **[Josquin Research Project](https://github.com/josquin-research-project/jrp-scores)** —
  [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)
  (attribution required, non-commercial use only).
- **[The 1520s Project](https://github.com/benory/1520s-project-scores)** —
  [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)
  (attribution required, non-commercial use only).
- **[Tasso in Music Project](https://github.com/TassoInMusicProject/tasso-scores)** —
  no license file published in the repo as of this writing; credited
  here, terms not otherwise specified by the project.
- **[SEILS](https://github.com/SEILSdataset/SEILSdataset)** —
  [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)
  (attribution required, non-commercial use only, share-alike).
- **[Lassus's Geistliche Psalmen](https://github.com/WolfgangDrescher/lassus-geistliche-psalmen)** —
  no license file published in the repo as of this writing; credited
  here, terms not otherwise specified by the project.

This app is a free, non-commercial side project with no ads or
monetization, consistent with every non-commercial term above. If
you're citing or reusing results from a specific piece, cite that
piece's own source collection (linked above), not just this app.

**Citing this app itself:** if you use it in published work — beyond
citing CRIM Intervals/music21 above for the actual analysis methods —
this repo has a [`CITATION.cff`](CITATION.cff) file; GitHub renders a
"Cite this repository" button from it (APA/BibTeX) in the sidebar.

This repo's own source code (not the musical data it aggregates, nor
the third-party libraries above — several of which are more
restrictive) is [MIT-licensed](LICENSE).

## Notes

- Humdrum (`.krn`) upload isn't offered in the "upload your own file" tab
  — CRIM's own reference app only demonstrates MEI/MusicXML uploads from
  text, so that's the tested, reliable set here too. (The five
  GitHub-backed collections — JRP, 1520s, Tasso, SEILS, Lassus Psalms —
  all handle `.krn` internally via their own known-good fetch, which is
  different from a user-supplied upload.)
- Nothing you upload is stored server-side; it only exists for the
  duration of your browser session.
