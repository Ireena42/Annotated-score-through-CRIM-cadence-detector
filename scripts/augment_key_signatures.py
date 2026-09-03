"""
scripts/augment_key_signatures.py

Adds a 'flats' field to every existing record in data/finalis.jsonl that
doesn't already have one -- the number of flats in that piece's own
encoded key signature (0, 1, 2, ... ; sharps recorded as a negative
number, -1/-2/..., so 'flats > 0' and 'flats == 1' checks stay simple
and unambiguous). Needed for the Browse tab's Modal family filter to
apply cantus-mollis transposition (see app.py's _MOLLIS_TRANSPOSITION)
across the WHOLE corpus, not just Palestrina (where it could be read
from a free local file, already done via corpus_sources.
_palestrina_key_signature_flats -- this script covers the other 6
collections/formats, which need one raw-content fetch per piece).

Does NOT touch finalis/source/detail -- those are precompute_finalis.py's
job. This only adds one new field to already-existing records, and only
for records that have a real finalis (nothing to filter by otherwise,
so nothing worth fetching for).

Per-collection key-signature source, each checked directly against a
real file before relying on it (not assumed from a format spec):
- music21/palestrina: raw Humdrum '*k[...]' line, local file, free.
- music21/monteverdi: MusicXML <fifths> element inside the .mxl zip,
  local file, free -- same UTF-16-detection dance _local_file_stats
  (app.py) already needs for these files (37 of 49 are UTF-16, not
  UTF-8, confirmed earlier this project).
- crim: MEI 'key.sig="Nf"' (or 'Ns' for sharps) attribute, on every
  <staffDef> -- confirmed directly against CRIM_Model_0001.mei, one
  fetch of the whole MEI file (no lighter endpoint exists).
- jrp/1520s/tasso/seils/lassus_psalms: raw Humdrum '*k[...]' line, same
  convention as Palestrina, fetched from GitHub raw.

Resumable: rewrites data/finalis.jsonl from an in-memory dict, skipping
any record that already has 'flats', so an interrupted run picks back
up without re-fetching what it already has. Checkpoints (rewrites the
whole file) every CHECKPOINT_EVERY pieces, not just at the end -- the
whole file is under 1MB, rewriting it periodically is cheap, and it's
the only way for a genuinely resumable run over ~3000 network fetches.

Usage (from the crim env):
    python scripts/augment_key_signatures.py [--limit N] [--collection C]
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import corpus_sources  # noqa: E402

FINALIS_PATH = Path(__file__).resolve().parent.parent / 'data' / 'finalis.jsonl'
CHECKPOINT_EVERY = 50

_KERN_KEYSIG_RE = re.compile(r'^\*k\[([a-g#-]*)\]')


def _flats_from_kern_text(text):
    for line in text.split('\n'):
        for token in line.split('\t'):
            m = _KERN_KEYSIG_RE.match(token)
            if m:
                body = m.group(1)
                if '#' in body:
                    return -body.count('#')
                return body.count('-')
    return 0


_MEI_KEYSIG_RE = re.compile(r'key\.sig="(\d+)([fs])"')


def _flats_from_mei_text(text):
    m = _MEI_KEYSIG_RE.search(text)
    if not m:
        return 0
    n, kind = int(m.group(1)), m.group(2)
    return -n if kind == 's' else n


def _flats_for_music21_palestrina(piece_id):
    members = corpus_sources._palestrina_movement_members().get(piece_id, [piece_id])
    flats_map = corpus_sources._palestrina_key_signature_flats()
    return flats_map.get(members[-1], 0)


def _flats_for_music21_monteverdi(piece_id):
    candidates = [p for p in corpus_sources.m21.corpus.getComposer('monteverdi') if p.stem == piece_id]
    real_scores = [p for p in candidates if p.suffix != '.rntxt']
    fp = real_scores[0] if real_scores else (candidates[0] if candidates else None)
    if fp is None or fp.suffix != '.mxl':
        return 0
    try:
        with zipfile.ZipFile(fp) as z:
            inner_name = next(n for n in z.namelist() if n.endswith('.xml') and 'META-INF' not in n)
            raw = z.read(inner_name)
        if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
            xml_text = raw.decode('utf-16')
        else:
            xml_text = raw.decode('utf-8', errors='replace')
    except Exception:
        return 0
    m = re.search(r'<fifths>(-?\d+)</fifths>', xml_text)
    if not m:
        return 0
    fifths = int(m.group(1))
    # MusicXML <fifths>: positive = sharps, negative = flats (circle-of-
    # fifths count) -- opposite sign convention from this script's own
    # 'flats' field, so flip it: fifths=-1 (one flat) -> flats=1.
    return -fifths


def flats_for_row(collection, native_ref):
    """Returns an int (0 = no flats/sharps, positive = N flats, negative
    = N sharps), or None if it couldn't be determined (network failure,
    unrecognized format) -- None is NOT written to the record, so a
    later run retries it rather than caching a wrong 0."""
    if collection == 'music21':
        corpus_key, piece_id = native_ref
        if corpus_key == 'palestrina':
            return _flats_for_music21_palestrina(piece_id)
        if corpus_key == 'monteverdi':
            return _flats_for_music21_monteverdi(piece_id)
        return None
    if collection == 'crim':
        if not native_ref['mei_links']:
            return None
        try:
            text = corpus_sources._get_with_retry(native_ref['mei_links'][0], timeout=30).text
        except Exception:
            return None
        return _flats_from_mei_text(text)
    # The 5 GitHub-hosted Humdrum kern collections.
    base_url = corpus_sources.KERN_COLLECTION_BASE_URLS.get(collection)
    if base_url is None:
        return None
    try:
        text = corpus_sources._get_with_retry(base_url + native_ref, timeout=20).text
    except Exception:
        return None
    return _flats_from_kern_text(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=None,
                         help='Stop after augmenting this many new records (for a scoped/test run).')
    parser.add_argument('--collection', default=None,
                         help='Only augment rows from this one collection (music21/crim/jrp/1520s/tasso/seils/lassus_psalms).')
    args = parser.parse_args()

    existing = {}
    if FINALIS_PATH.exists():
        with FINALIS_PATH.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                existing[record['label']] = record
    print(f"Loaded {len(existing)} existing records.", flush=True)

    rows = corpus_sources.build_browse_index()
    todo = [
        row for row in rows
        if row[0] in existing
        and existing[row[0]].get('finalis')
        and 'flats' not in existing[row[0]]
        and (args.collection is None or row[1] == args.collection)
    ]
    print(f"{len(todo)} record(s) with a real finalis still need a 'flats' value.", flush=True)

    done = 0
    errors = 0
    for i, (label, collection, native_ref) in enumerate(todo):
        if args.limit is not None and done >= args.limit:
            print(f"Hit --limit {args.limit}, stopping.", flush=True)
            break
        flats = flats_for_row(collection, native_ref)
        if flats is None:
            errors += 1
        else:
            existing[label]['flats'] = flats
        done += 1
        if done % CHECKPOINT_EVERY == 0:
            _write_all(existing)
            print(f"{done}/{len(todo)} done ({errors} errors so far), checkpointed.", flush=True)

    _write_all(existing)
    print(f"FINAL: {done} processed, {errors} errors, checkpointed to {FINALIS_PATH}.", flush=True)


def _write_all(existing):
    with FINALIS_PATH.open('w', encoding='utf-8') as f:
        for record in existing.values():
            f.write(json.dumps(record, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
