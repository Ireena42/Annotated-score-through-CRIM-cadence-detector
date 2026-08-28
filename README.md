# Renaissance Score Workbench

Renaissance polyphony is scattered across dozens of separate archives
online. This app gathers **~4,300 pieces from 7 of them** into one
searchable, analysis-ready place. Run [CRIM Intervals](https://github.com/HCDigitalScholarship/intervals)'s
structural analyses — cadences (e.g. `Authentic → G`), points of
imitation, and homorhythmic passages — on any piece, see where they
fall across its structure, and take the results further: an annotated
score marked **directly onto the notation itself** (ready for MuseScore,
Finale, or any notation software), a raw score for your own code, or a
CSV dataset across a whole search.

CRIM Intervals (Morgan & Freedman, CRIM Project) analyzes contrapuntal
voice functions (Cantizans, Tenorizans, Bassizans, Altizans, etc.) rather
than just harmonic labeling — built for exactly this repertoire. This
tool adds what CRIM's own output doesn't give you on its own: the
results written back into a real, readable score file, plotted across
the piece, or exported as data ready for your own analysis.

Started as a single-purpose "Cadence Annotator"; renamed once the
collection-aggregation side grew into something worth naming on its own.
Built as a side tool for a physics thesis on network-science analysis of
Palestrina's polyphony — the app itself has no dependency on that project.

**[Open the live app](https://renaissance-score-workbench.streamlit.app/)**
— no install needed, just a browser. First load after a quiet spell can
take 30-60s while it wakes up (Streamlit Community Cloud's free tier
sleeps idle apps); after that it's fast.

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://renaissance-score-workbench.streamlit.app/)

## Use it

**🔍 Browse all** — search all ~4,300 pieces across every collection at
once by composer or title. Pick a match, hit **Preview** to see
its voice count and whether it has encoded text/lyrics before committing
to anything heavier, or go straight to **Download**.

Every search also gets a **"📄 Download all N match(es) as CSV"**
button — a manifest of *every* match (not capped to the 50 shown in the
picker below it): collection, composer, the display label, and a
source URL or `music21` corpus path for each, meant to be loaded with
`pandas` and fetched/parsed directly in your own script, no dependency
on this app at all. Handy for "give me every Josquin piece across all
7 collections to analyze myself" — search "Josquin", download the CSV.

Or pick a piece from one collection directly:
- **music21 corpus** — Palestrina (~1300 pieces) or Monteverdi, picked by
  composer then a real title (e.g. "Missa Ad coenam Agni: Agnus I"), not
  an internal file id
- **CRIM Project corpus** — 359 pieces: Lassus's parody masses plus the
  motets/chansons/madrigals they're modeled on, live from crimproject.org.
  Filterable by **genre** (Motet/Madrigal/Mass movement/Chanson/...) —
  the only one of the 7 collections where genre exists as real per-piece
  data, so the filter lives here rather than on Browse.
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
**Download**:
- **"Annotate cadences"** — CRIM's `cadences()`, marked in red. On by
  default, since it's this app's original and still-primary feature.
- **"Also mark points of imitation"** — CRIM's `presentationTypes()`,
  which finds where a melodic subject enters in one voice and is
  imitated by others (a Point of Entry, Imitative Duo, or Fuga), marked
  in blue. Off by default.
- **"Also mark homorhythmic passages"** — CRIM's `homorhythm()`, which
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
   123-measure/6-voice piece).
4. **Browse all** reuses every collection's own piece list (built once,
   cached) plus a lightweight per-piece check (already-available metadata
   for CRIM/music21; a single small-file fetch for the five kern-backed
   collections) to preview voice count and text/lyric encoding without
   running the full pipeline first.
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
7. The **strip plot** reads the `Progress` column every one of CRIM's
   three analyses already computes on its own (offset divided by the
   piece's last note offset, 0-1) — no separate calculation needed, just
   collecting a column CRIM already gives back.

## Notes

- Humdrum (`.krn`) upload isn't offered in the "upload your own file" tab
  — CRIM's own reference app only demonstrates MEI/MusicXML uploads from
  text, so that's the tested, reliable set here too. (The five
  GitHub-backed collections — JRP, 1520s, Tasso, SEILS, Lassus Psalms —
  all handle `.krn` internally via their own known-good fetch, which is
  different from a user-supplied upload.)
- Nothing you upload is stored server-side; it only exists for the
  duration of your browser session.
