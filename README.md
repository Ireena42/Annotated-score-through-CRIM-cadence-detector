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
any notation software), a raw score for your own code, or a CSV dataset
across a whole search.

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
sleeps idle apps); after that it's fast.

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

Every search also gets two bulk downloads, covering *every* match, not
just the 50 shown in the picker below them:
- **"📄 Download all N match(es) as CSV"** — a manifest (collection,
  composer, the display label, and a source URL or `music21` corpus
  path for each) meant to be loaded with `pandas` and fetched/parsed
  directly in your own script, no dependency on this app at all. Any
  number of matches, always available.
- **"📦 Build a ZIP of all N score(s) (MusicXML)"** — actually fetches,
  parses, and converts every match to a real MusicXML file and zips
  them, no CRIM analysis run on any of them. Capped at 30 matches at a
  time (benchmarked directly at ~5s/piece — past 30 it's a bad wait for
  a single click, and the CSV is a better fit for a bigger set anyway).
  A piece that fails to fetch is skipped and reported by name, not
  silently dropped from the count.

A third option below those two, checkboxes and all — **"🧮 Build
analysis-data CSV(s) for all N piece(s)"** — actually *runs* the checked
analyses (cadences/points of imitation/homorhythm) across every match
and hands back CRIM's own raw columns (`CadType`/`Tone`/`RelTone` for
cadences, `Presentation_Type`/`Soggetti`/`Voices` for points of
imitation, `hr_voices` for homorhythm), one CSV per analysis, each row
tagged with `collection`/`composer`/`label` so pieces stay identifiable
once every match is concatenated together — ready for your own stats,
not just a count of what was found. Also builds a **per-piece density
comparison CSV** — one row per piece, one column per checked analysis,
the same density figure (fraction of measures touched — see below) a
single-piece Analyze shows, so several pieces can be compared side by
side directly, which the per-event CSVs above don't make easy on their
own. A blank cell means that analysis wasn't computed for that piece at
all (never requested, or it failed); a `0` means it genuinely found
nothing — kept distinct rather than collapsed into one blank. This is
real per-piece CRIM computation on top of the fetch, so it's capped
tighter than the ZIP (15 matches) — a provisional cap, not yet
benchmarked live the way the ZIP's 30 was.

Handy for "give me every Josquin piece across all 7 collections to
analyze myself" — search "Josquin", download the CSV (or, narrowed
further, the ZIP or the analysis-data CSVs).

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
("Download annotated MusicXML" vs. plain "Download MusicXML").

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
   crashing the whole annotation.
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

## Finalis precompute (in progress, not yet in the app's UI)

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
- `.github/workflows/precompute_finalis.yml` — manually triggered
  (`workflow_dispatch`, from the Actions tab), since a full run is a
  genuinely multi-hour job, almost certainly longer than one workflow
  run's own time limit — expect to trigger this several times over
  several days, each pass resuming from the last one's committed
  progress, to work through the full corpus.

Now that the 5-piece test has confirmed the pipeline itself works, the
next step is triggering a full run (blank `limit`, all collections) —
realistically over several separate `workflow_dispatch` triggers, each
resuming from the last one's committed progress (see `data/
finalis.jsonl`'s growing piece count). Once that's done and the numbers
still look right at scale, the remaining step — reading the file and
wiring up an actual Finalis filter widget in Browse — hasn't been built
yet.

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

## Notes

- Humdrum (`.krn`) upload isn't offered in the "upload your own file" tab
  — CRIM's own reference app only demonstrates MEI/MusicXML uploads from
  text, so that's the tested, reliable set here too. (The five
  GitHub-backed collections — JRP, 1520s, Tasso, SEILS, Lassus Psalms —
  all handle `.krn` internally via their own known-good fetch, which is
  different from a user-supplied upload.)
- Nothing you upload is stored server-side; it only exists for the
  duration of your browser session.
