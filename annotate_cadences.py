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


def _measure_index(part):
    """{measure_number: Measure} for one part, built in a single pass.

    Exists because repeatedly calling part.measure(n) is expensive and
    scales badly: measured directly on a 123-measure/6-voice piece, 200
    varying-argument calls took ~6s, and the annotation step for that
    piece's 52 cadences (which calls the equivalent of .measure() several
    times per cadence -- once per cadential-role voice, plus once for the
    label) took 13+ seconds total, dwarfing every other stage of the
    pipeline (fetch+parse+cadence-detection combined: ~5s). Verified this
    index produces identical results to part.measure(n) across every
    measure of a real piece (0 mismatches, exact object identity) before
    switching to it -- not just assumed equivalent."""
    return {m.number: m for m in part.getElementsByClass('Measure')}


def find_note_at_beat(measure, beat, tol=BEAT_TOLERANCE):
    """The Note/Chord in `measure` whose onset beat matches `beat` -- i.e.
    the note actually performing a role at the cadence's perfection. None
    if `measure` is None, or nothing attacks exactly at `beat` (the voice
    may be sustaining a tied note into the perfection rather than
    attacking on it)."""
    if measure is None:
        return None
    for n in measure.recurse().notes:
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
    # built once per part, not once per cadence -- see _measure_index's
    # docstring for the measured cost of not doing this
    measure_indices = [_measure_index(p) for p in parts]

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
                note_obj = find_note_at_beat(measure_indices[idx].get(measure_no), beat)
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
        target_measure = measure_indices[0].get(measure_no)
        # getContextByClass can genuinely come back None -- confirmed
        # directly: re-exporting a score to MusicXML and re-importing it
        # (exactly what this app's own Download -> re-Upload cycle does)
        # can leave a measure with no TimeSignature discoverable via
        # context, even though the original parse had one. Treated the
        # same as "no matching measure" (n_missed_label) rather than
        # crashing the whole annotation over one unplaceable label.
        ts = target_measure.getContextByClass('TimeSignature') if target_measure is not None else None
        if ts is not None:
            te = expressions.TextExpression(cadence_label(row))
            te.style.absoluteY = 20  # nudge clear of the staff/notes
            target_measure.insert(ts.getOffsetFromBeat(beat), te)
            n_labeled += 1
        else:
            n_missed_label += 1

    return score, {'labeled': n_labeled, 'missed_label': n_missed_label, 'colored': n_colored}


PRESENTATION_COLOR = '#2266CC'  # distinct from cadence red, so both survive on one score

# CRIM's own category codes, translated for the label that actually goes
# on the score. 'FUGA' in particular reads as a false claim to anyone not
# already steeped in 16th-century terminology -- it's CRIM's classifier
# name for the general/catch-all case (usually 3+ voices), using the
# period Latin/Italian sense of "fuga" (voices "fleeing" one after
# another), not the fixed Baroque form the word suggests to a modern
# reader glancing at a score. CRIM's own raw codes are kept as a
# cross-reference in the app's explanation text, just never printed onto
# the exported score itself, which is what a request explicitly asked for.
PRESENTATION_TYPE_LABELS = {
    'PEN': 'Point of Entry',
    'ID': 'Imitative Duo',
    'FUGA': 'Imitative Entry',
}


def annotate_presentation_types(score, ptypes, part_names):
    """Marks each point-of-imitation entry directly on the score: colors
    the note where each voice's entry begins, and labels the FIRST entry
    of each instance with its type in plain language (see
    PRESENTATION_TYPE_LABELS -- NOT CRIM's raw 'PEN'/'ID'/'FUGA' codes,
    which don't read cleanly out of context on an actual score) --
    placed on the top staff, same visual-consistency reasoning as
    cadence labels above (always reads above the system, regardless of
    which voice actually enters first).

    score: a music21 Score (mutated in place AND returned).
    ptypes: a DataFrame like crim_intervals' piece.presentationTypes()
        output -- 'Measures_Beats' (list of 'measure/beat' strings) and
        'Voices' (list of part-name strings, same length/order) per row.
    part_names: the ordered list from ImportedPiece._getPartNames() for
        this same score, passed in explicitly rather than recomputed
        here -- this is CRIM's own already-verified voice-naming
        convention (duplicate part names disambiguated as 'Part-2',
        'Part-3', ... in part order), not something worth
        reimplementing independently and risking drift from the real
        rule (confirmed directly against a real piece before relying
        on it, not assumed from the docstring alone).

    Returns (score, stats) where stats = {'labeled', 'missed_label', 'colored'}.
    """
    parts = list(score.parts)
    name_to_index = {name: i for i, name in enumerate(part_names)}
    measure_indices = [_measure_index(p) for p in parts]

    n_labeled, n_missed_label, n_colored = 0, 0, 0
    for _, row in ptypes.iterrows():
        first_labeled = False
        for measure_beat, voice_name in zip(row['Measures_Beats'], row['Voices']):
            measure_str, beat_str = measure_beat.split('/')
            measure_no, beat = int(float(measure_str)), float(beat_str)
            idx = name_to_index.get(voice_name)
            if idx is None or idx >= len(parts):
                continue
            note_obj = find_note_at_beat(measure_indices[idx].get(measure_no), beat)
            if note_obj is not None:
                note_obj.style.color = PRESENTATION_COLOR
                n_colored += 1
            if not first_labeled:
                target_measure = measure_indices[0].get(measure_no)
                # ts can genuinely be None -- see annotate_score's comment
                # on the same pattern. Unlike a cadence (one shot), an
                # imitation instance has several entries to try: if THIS
                # entry's measure has no TimeSignature context, don't set
                # first_labeled yet, so the next voice's entry gets a shot
                # at being the one that's labeled instead of giving up on
                # the whole instance over one unplaceable measure.
                ts = target_measure.getContextByClass('TimeSignature') if target_measure is not None else None
                if ts is not None:
                    label_text = PRESENTATION_TYPE_LABELS.get(
                        row['Presentation_Type'], row['Presentation_Type']
                    )
                    te = expressions.TextExpression(label_text)
                    te.style.absoluteY = 40  # above cadence labels (20), to reduce collision
                    te.style.color = PRESENTATION_COLOR
                    target_measure.insert(ts.getOffsetFromBeat(beat), te)
                    n_labeled += 1
                    first_labeled = True
        if not first_labeled:
            n_missed_label += 1

    return score, {'labeled': n_labeled, 'missed_label': n_missed_label, 'colored': n_colored}


HOMORHYTHM_COLOR = '#22AA55'  # green -- third color, distinct from cadence red and imitation blue


def annotate_homorhythm(score, hr, part_names, min_gap=4.0):
    """Marks each homorhythmic (chordal, shared-text-declamation) passage
    on the score: colors every note in every voice listed in that row's
    'hr_voices', and labels one point per passage -- placed higher than
    both cadence (y=20) and presentation-type (y=40) labels so all three
    features can coexist without overlapping text.

    score: a music21 Score (mutated in place AND returned).
    hr: a DataFrame like crim_intervals' piece.homorhythm() output --
        MultiIndex (Measure, Beat, Offset), a 'hr_voices' column (list of
        part-name strings, same naming convention as presentationTypes'
        'Voices' -- confirmed directly, including a real piece where a
        part is literally named '[Superius]' with brackets in the source
        data itself, not a formatting artifact).
    part_names: the ordered list from ImportedPiece._getPartNames() for
        this same score -- see annotate_presentation_types for why this
        is passed in rather than reimplemented.
    min_gap: homorhythm() returns raw overlapping n-gram matches, NOT
        pre-merged into distinct passages the way presentationTypes()
        already is (checked directly in its source -- no such
        consolidation step exists there) -- consecutive rows can be as
        little as 2 offset-units apart, all describing the same
        underlying passage through a sliding window. Every matching note
        still gets colored (so the passage's full extent is visible), but
        a new text label is only added when a row's offset is more than
        `min_gap` past the last labeled one, collapsing what would
        otherwise be a label every beat or two into one per passage.
        Default (4.0) matches homorhythm()'s own default ngram_length.

    Returns (score, stats) where stats = {'labeled', 'missed_label', 'colored'}.
    """
    parts = list(score.parts)
    name_to_index = {name: i for i, name in enumerate(part_names)}
    measure_indices = [_measure_index(p) for p in parts]

    n_labeled, n_missed_label, n_colored = 0, 0, 0
    last_labeled_offset = None
    for (measure_no, beat, offset), row in hr.iterrows():
        measure_no, beat = int(measure_no), float(beat)
        any_colored = False
        for voice_name in row['hr_voices']:
            idx = name_to_index.get(voice_name)
            if idx is None or idx >= len(parts):
                continue
            note_obj = find_note_at_beat(measure_indices[idx].get(measure_no), beat)
            if note_obj is not None:
                note_obj.style.color = HOMORHYTHM_COLOR
                n_colored += 1
                any_colored = True

        is_new_passage = last_labeled_offset is None or offset - last_labeled_offset > min_gap
        if any_colored and is_new_passage:
            target_measure = measure_indices[0].get(measure_no)
            # ts can genuinely be None -- see annotate_score's comment on
            # the same pattern (a re-exported/re-imported score can leave
            # a measure with no TimeSignature discoverable via context).
            ts = target_measure.getContextByClass('TimeSignature') if target_measure is not None else None
            if ts is not None:
                te = expressions.TextExpression('Homorhythm')
                te.style.absoluteY = 60  # above cadence (20) and imitation (40) labels
                te.style.color = HOMORHYTHM_COLOR
                target_measure.insert(ts.getOffsetFromBeat(beat), te)
                n_labeled += 1
            else:
                n_missed_label += 1
            last_labeled_offset = offset

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
