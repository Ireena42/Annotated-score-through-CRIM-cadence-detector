"""
RUN IN THE `complexp` CONDA ENV. music21 only -- no crim_intervals import
here, so this does NOT need the `crim` env (unlike crim_export_cadences.py,
which must produce this script's input first).

Reads a piece's exported MusicXML plus its CRIM cadences table (WITH the
'PartMap' column -- produced by crim_export_cadences.py in the `crim` env)
and writes a new MusicXML with every cadence marked directly on the score:
  - a text label above the top staff, at the cadence's exact beat, e.g.
    "Authentic -> G"
  - the note(s) that actually performed each cadential role (per PartMap)
    recolored, so the cadential sonority is visible at a glance

PartMap position numbering (1 = highest staff) is CRIM's own convention
and lines up directly with `score.parts` order in music21: both walk the
score's Part elements in the same top-to-bottom document order (confirmed
against crim_intervals' ImportedPiece._getFlatParts/numberParts source,
per this project's "verify library behavior by reading source" rule).

Usage (from the complexp env):
    python annotate_cadences.py <piece.xml> <cadences_with_partmap.csv> <out.xml>
"""
import json
from pathlib import Path

import pandas as pd
from music21 import converter, expressions

CADENCE_COLOR = '#CC3333'
BEAT_TOLERANCE = 1e-3


def cadence_label(row):
    """e.g. 'Authentic -> G'. Some rows have a CVF combination CRIM tracks
    but doesn't assign a named CadType to (CadType is blank in the raw
    cadences.csv, e.g. an Agnus_00 row with CVFs='TCu') -- fall back to
    showing the CVFs string itself in that case, e.g. '[TCu] -> D'."""
    cad_type = row['CadType'] if pd.notna(row['CadType']) else f"[{row['CVFs']}]"
    # Tone is NaN when the Cantizans CVF was evaded/abandoned (see
    # crim_intervals docstring); fall back to the lowest sounding pitch,
    # same convention already used for this comparison in findings.md #16.
    tone = row['Tone'] if pd.notna(row['Tone']) else row['Low']
    return f"{cad_type} → {tone}"


def find_note_at_beat(part, measure_number, beat, tol=BEAT_TOLERANCE):
    """The Note/Chord in `part`'s given measure whose onset beat matches
    `beat` -- i.e. the note actually performing a role at the cadence's
    perfection. None if nothing attacks exactly there (the voice may be
    sustaining a tied note into the perfection rather than attacking on it)."""
    m = part.measure(measure_number)
    if m is None:
        return None
    for n in m.recurse().notes:
        if abs(n.beat - beat) < tol:
            return n
    return None


def annotate_score(score, cadences):
    """Core annotation step, factored out of annotate_piece() so callers
    that already have a music21 Score in memory (e.g. app.py, which parses
    an uploaded file directly) don't need to round-trip through disk.

    score: a music21 Score (mutated in place AND returned, for convenience).
    cadences: a DataFrame like crim_intervals' piece.cadences(voice_detail=
        True) output, with a 'PartMap' column of already-decoded dicts
        (NOT the JSON-string form used in the on-disk CSV -- see
        annotate_piece() below for that conversion).

    Returns (score, stats) where stats = {'labeled', 'missed_label', 'colored'}
    counts, for the caller to report however it likes (print, st.write, ...).
    """
    parts = list(score.parts)  # index 0 = highest staff = PartMap position '1'
    top_part = parts[0]

    n_labeled, n_missed_label, n_colored = 0, 0, 0
    for _, row in cadences.iterrows():
        measure_no, beat = int(row['Measure']), float(row['Beat'])

        # recolor each cadential-role voice's arrival note (PartMap:
        # {CVF letter -> [staff positions]}, e.g. {'C': ['2'], 'B': ['5']})
        for staff_positions in row['PartMap'].values():
            for pos in staff_positions:
                idx = int(pos) - 1
                if idx >= len(parts):
                    continue
                note_obj = find_note_at_beat(parts[idx], measure_no, beat)
                if note_obj is not None:
                    note_obj.style.color = CADENCE_COLOR
                    n_colored += 1

        # text label, always shown above the top staff. Must be inserted
        # into the top staff's actual Measure object (not the flat Part
        # stream) -- once a score already has Measures (true here, since
        # this is a parsed/imported piece, not one built fresh), musicxml
        # writing only looks inside existing Measures for content; anything
        # inserted straight into the Part at an offset is silently dropped
        # (confirmed by a minimal repro before landing on this approach).
        # Beat -> offset-within-measure comes straight from the measure's
        # own TimeSignature, so this doesn't depend on any voice actually
        # attacking a note at this exact beat (the earlier attempt, timing
        # the label off a role-voice note, still missed cases where no
        # voice's attack lined up with floating-point beat equality).
        target_measure = top_part.measure(measure_no)
        if target_measure is not None:
            ts = target_measure.getContextByClass('TimeSignature')
            te = expressions.TextExpression(cadence_label(row))
            te.style.absoluteY = 20  # nudge clear of the staff/notes
            target_measure.insert(ts.getOffsetFromBeat(beat), te)
            n_labeled += 1
        else:
            n_missed_label += 1

    return score, {'labeled': n_labeled, 'missed_label': n_missed_label, 'colored': n_colored}


def annotate_piece(xml_path, cadences_csv, out_path):
    """CLI entry point: reads everything from disk, calls annotate_score(),
    writes the result back to disk. Kept as a thin wrapper around
    annotate_score() so this file's on-disk behaviour (used by
    annotate_piece_pipeline.py) didn't need to change when app.py needed
    an in-memory version of the same logic."""
    score = converter.parse(str(xml_path))
    cadences = pd.read_csv(cadences_csv)
    cadences['PartMap'] = cadences['PartMap'].apply(json.loads)

    score, stats = annotate_score(score, cadences)
    print(f"{len(cadences)} cadences: {stats['labeled']} labeled "
          f"({stats['missed_label']} anchor(s) not found), {stats['colored']} role-notes colored")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    score.write('musicxml', fp=str(out_path))
    return out_path


if __name__ == '__main__':
    import sys
    xml_path, cadences_csv, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    annotate_piece(xml_path, cadences_csv, out_path)
