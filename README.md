# Cadence Annotator

A small Streamlit app that detects cadences in a Renaissance polyphonic
score and writes them **directly onto the score itself** — a text label
(e.g. `Authentic → G`) plus colored noteheads at every cadential voice —
so you can open the result in MuseScore, Finale, or any notation software
and see exactly where and how each cadence happens.

Cadence detection is done by [CRIM Intervals](https://github.com/HCDigitalScholarship/intervals)
(Morgan & Freedman, CRIM Project), which analyzes contrapuntal voice
functions (Cantizans, Tenorizans, Bassizans, Altizans, etc.) rather than
just harmonic labeling — built for exactly this repertoire. This tool adds
the missing last step: writing those results back into a real, readable
score file instead of only a table or an interactive chart.

## Use it

Two ways to give it a piece:
- pick one from music21's bundled Palestrina corpus (a dropdown of ids)
- upload your own MusicXML (`.xml`/`.musicxml`) or MEI (`.mei`) file

Click **Annotate**, then download the resulting `.xml`.

## Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens at `http://localhost:8501`.

## How it works, briefly

1. The score is parsed with [music21](https://www.music21.org/).
2. `crim_export_cadences.py`'s logic (folded into `app.py` for the live
   app; kept as a standalone script too, for command-line/batch use) runs
   CRIM's `.cadences(voice_detail=True)`, which — beyond the usual cadence
   type/tone/measure columns — also returns a `PartMap`: which staff
   performed each cadential role (Cantizans, Tenorizans, Bassizans, ...)
   at each cadence.
3. `annotate_cadences.py` uses `PartMap` to find the exact notes involved
   and colors them, and inserts a text label into the score's measure
   structure at the cadence's precise beat.

## Notes

- Humdrum (`.krn`) upload isn't offered — CRIM's own reference app only
  demonstrates MEI/MusicXML uploads-from-text, so that's the tested,
  reliable set here too.
- Nothing you upload is stored server-side; it only exists for the
  duration of your browser session.
