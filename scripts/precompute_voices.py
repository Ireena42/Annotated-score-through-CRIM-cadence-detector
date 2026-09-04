"""
scripts/precompute_voices.py

Precomputes voice count (number of vocal parts) per piece, for every
piece across all 7 collections, into data/voices.jsonl -- feeds the
Browse tab's "Number of voices" filter (app.py), which needs this
available for ~4,300 pieces without a live per-row network fetch on
every Streamlit rerun (the same reason data/finalis.jsonl exists for
the Modal family filter -- this follows that same design, not a new
one).

Keyed by the GROUPED Browse label (corpus_sources.group_browse_rows'
own output), not the raw per-file label data/finalis.jsonl uses --
deliberately different from that file's own convention, because the
two features need different aggregation semantics for a Palestrina
movement split across several encoded files (see corpus_sources.
merge_movement_parts' own docstring for why these exist): a finalis is
only meaningful read off the piece's true ENDING, so finalis.jsonl
resolves a grouped row to its LAST real member (see app.py's
last_member_label); a voice count should reflect the movement's overall
scoring, so this file takes the MAX across every real member instead --
the same convention app.py's own preview_piece() already uses live for
a single piece, reused here for consistency between what a live preview
shows and what this batch precompute records for the exact same piece.
Because of this, this file's own key already matches exactly what the
Browse "All" tab's grouped index displays and filters -- no extra
per-row label translation is needed at lookup time the way the Modal
family filter needs (see app.py's own _finalis_lookup_label).

Per-collection source, each already checked directly elsewhere in this
project before being reused here (not re-verified from scratch):
- crim: 'number_of_voices' field, already free in the CRIM piece-list
  JSON (corpus_sources.fetch_crim_pieces()) -- no per-piece fetch at
  all needed.
- music21/palestrina: corpus_sources._palestrina_voice_counts(), a
  cheap local '**kern' spine-line scan (same file, same convention
  corpus_sources._palestrina_key_signature_flats already uses for
  flats) -- a grouped multi-part movement takes the MAX across its
  real members' own counts (see corpus_sources._palestrina_movement_
  members), same approximation app.py's preview_piece() already uses
  live, not a new rule invented here.
- music21/monteverdi: local '.mxl' file, '<score-part ' tag count
  inside the zipped MusicXML -- same UTF-16-aware read scripts/
  augment_key_signatures.py already needed for these same files (76%
  of them are internally UTF-16, not UTF-8 -- confirmed there).
- jrp/1520s/tasso/seils/lassus_psalms: one raw Humdrum fetch per piece,
  '**kern' token count on the spine-declaration line -- same
  convention app.py's preview_piece() already uses live for these.

Resumable: rewrites data/voices.jsonl from an in-memory dict, skipping
any record that already has a value, so an interrupted run picks back
up without re-fetching what it already has. Checkpoints every
CHECKPOINT_EVERY pieces, not just at the end -- same discipline as
scripts/augment_key_signatures.py, for the same reason (a genuinely
resumable run over ~2,500 network fetches for the 5 kern collections).

Usage (from the crim env):
    python scripts/precompute_voices.py [--limit N] [--collection C]
"""
import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import corpus_sources  # noqa: E402
import music21 as m21  # noqa: E402

VOICES_PATH = Path(__file__).resolve().parent.parent / 'data' / 'voices.jsonl'
CHECKPOINT_EVERY = 50


def _voices_from_kern_text(text):
    spine_line = next((line for line in text.split('\n') if line.startswith('**')), '')
    return spine_line.count('**kern') or None


def _voices_for_music21_palestrina(group_key):
    # _palestrina_movement_members().get(x, [x]) handles BOTH a real
    # group key (multi-part movement) and an already-single-file
    # movement uniformly -- the latter just returns [group_key] itself,
    # so no separate branch is needed for the ~40% of movements that
    # were never split.
    members = corpus_sources._palestrina_movement_members().get(group_key, [group_key])
    counts = corpus_sources._palestrina_voice_counts()
    values = [counts[m] for m in members if counts.get(m)]
    return max(values) if values else None


def _voices_for_music21_monteverdi(piece_id):
    candidates = [p for p in m21.corpus.getComposer('monteverdi') if p.stem == piece_id]
    real_scores = [p for p in candidates if p.suffix != '.rntxt']
    fp = real_scores[0] if real_scores else (candidates[0] if candidates else None)
    if fp is None or fp.suffix != '.mxl':
        return None
    try:
        with zipfile.ZipFile(fp) as z:
            inner_name = next(n for n in z.namelist() if n.endswith('.xml') and 'META-INF' not in n)
            raw = z.read(inner_name)
        if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
            xml_text = raw.decode('utf-16')
        else:
            xml_text = raw.decode('utf-8', errors='replace')
    except Exception:
        return None
    return xml_text.count('<score-part ') or None


def voices_for_row(collection, native_ref):
    """Returns an int (>=1), or None if it couldn't be determined
    (network failure, unrecognized format) -- None is NOT written to
    the record, so a later run retries it rather than caching a wrong
    value."""
    if collection == 'crim':
        # 'or None': checked directly and found 74 CRIM pieces (all
        # Ludwig Daser / Victoria motets, confirmed by inspecting the
        # actual records) carry a literal 0 here -- a real gap in
        # CRIM's own catalog, not a genuine 0-voice piece. Treated the
        # same as any other undeterminable case (None, not written),
        # not silently stored as a wrong value.
        return native_ref.get('number_of_voices') or None
    if collection == 'music21':
        corpus_key, piece_id = native_ref
        if corpus_key == 'palestrina':
            return _voices_for_music21_palestrina(piece_id)
        if corpus_key == 'monteverdi':
            return _voices_for_music21_monteverdi(piece_id)
        return None
    base_url = corpus_sources.KERN_COLLECTION_BASE_URLS.get(collection)
    if base_url is None:
        return None
    try:
        text = corpus_sources._get_with_retry(base_url + native_ref, timeout=20).text
    except Exception:
        return None
    return _voices_from_kern_text(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=None,
                         help='Stop after computing this many new records (for a scoped/test run).')
    parser.add_argument('--collection', default=None,
                         help='Only compute rows from this one collection (music21/crim/jrp/1520s/tasso/seils/lassus_psalms).')
    args = parser.parse_args()

    existing = {}
    if VOICES_PATH.exists():
        with VOICES_PATH.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                existing[record['label']] = record
    print(f"Loaded {len(existing)} existing records.", flush=True)

    rows = corpus_sources.group_browse_rows(corpus_sources.build_browse_index())
    todo = [
        row for row in rows
        if row[0] not in existing
        and (args.collection is None or row[1] == args.collection)
    ]
    print(f"{len(todo)} record(s) still need a voice count.", flush=True)

    done = 0
    errors = 0
    for i, (label, collection, native_ref) in enumerate(todo):
        if args.limit is not None and done >= args.limit:
            print(f"Hit --limit {args.limit}, stopping.", flush=True)
            break
        voices = voices_for_row(collection, native_ref)
        if voices is None:
            errors += 1
        else:
            existing[label] = {'label': label, 'collection': collection, 'voices': voices}
        done += 1
        if done % CHECKPOINT_EVERY == 0:
            _write_all(existing)
            print(f"{done}/{len(todo)} done ({errors} errors so far), checkpointed.", flush=True)

    _write_all(existing)
    print(f"FINAL: {done} processed, {errors} errors, checkpointed to {VOICES_PATH}.", flush=True)


def _write_all(existing):
    with VOICES_PATH.open('w', encoding='utf-8') as f:
        for record in existing.values():
            f.write(json.dumps(record, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
