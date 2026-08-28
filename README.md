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

Built as a side tool for a physics thesis on network-science analysis of
Palestrina's polyphony — the app itself has no dependency on that project.

**[Open the live app](https://annotated-score-through-crim-cadence-detector-fdphigcb95z2rtfq.streamlit.app/)**
— no install needed, just a browser. First load after a quiet spell can
take 30-60s while it wakes up (Streamlit Community Cloud's free tier
sleeps idle apps); after that it's fast.

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://annotated-score-through-crim-cadence-detector-fdphigcb95z2rtfq.streamlit.app/)

## Use it

Seven ways to give it a piece:
- **music21 corpus** — Palestrina (~1300 pieces) or Monteverdi, picked by
  composer then a real title (e.g. "Missa Ad coenam Agni: Agnus I"), not
  an internal file id
- **CRIM Project corpus** — 359 pieces: Lassus's parody masses plus the
  motets/chansons/madrigals they're modeled on, live from crimproject.org
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
- **upload your own file** — MusicXML (`.xml`/`.musicxml`) or MEI (`.mei`)

Click **Annotate**, then download the resulting `.xml`. Two in-app
expanders explain what the cadence labels mean and how the detector
actually works, in case any of it isn't self-explanatory at a glance.

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
2. CRIM's `.cadences(voice_detail=True)` is run on it, which — beyond the
   usual cadence type/tone/measure columns — also returns a `PartMap`:
   which staff performed each cadential role (Cantizans, Tenorizans,
   Bassizans, ...) at each cadence.
3. `annotate_cadences.py` uses `PartMap` to find the exact notes involved
   and colors them, and inserts a text label into the score's measure
   structure at the cadence's precise beat.

## Notes

- Humdrum (`.krn`) upload isn't offered in the "upload your own file" tab
  — CRIM's own reference app only demonstrates MEI/MusicXML uploads from
  text, so that's the tested, reliable set here too. (The five
  GitHub-backed tabs — JRP, 1520s, Tasso, SEILS, Lassus Psalms — all
  handle `.krn` internally via their own known-good fetch, which is
  different from a user-supplied upload.)
- Nothing you upload is stored server-side; it only exists for the
  duration of your browser session.
