"""
scripts/precompute_finalis.py

Batch-computes a heuristic Finalis for every piece across all 7
collections this app knows about, and appends results to a JSON Lines
file (default data/finalis.jsonl), keyed by each piece's own Browse
label (the exact string corpus_sources.build_browse_index() already
produces -- no separate key scheme needed; Browse can look a result up
directly by the label it already holds in memory).

Finalis heuristic (see this repo's own conversation history for the
reasoning): the goal pitch class of the piece's LAST detected cadence,
in the lowest sounding voice -- the "clausula basizans" convention,
which is how this corpus's one hand-verified finalis (Agnus_00 = G) was
actually confirmed, and a more musicologically grounded read than just
"the literal last note," which is all crim_intervals' own .final()
method does. Falls back to .final() only when a piece has zero
detected cadences at all (a real, non-rare case -- see _safe_cadences's
own docstring in app.py for the crim_intervals bug this already guards
against elsewhere). Every record notes which of the two produced its
Finalis (`source`: 'cadence' or 'final_fallback'), or records an
`error` with no guess at all rather than a silently wrong one.

THIS SCRIPT HAS NOT BEEN RUN EVEN ONCE as of writing it -- no local
Python environment with music21/crim_intervals was available while
building it (see the conversation this came out of). Run a small test
FIRST:

    python scripts/precompute_finalis.py --limit 5 --collection music21

before trusting it with the full ~4,300-piece corpus. If that small run
produces reasonable-looking Finalis values (spot-check Agnus_00
specifically -- it should come back 'G', 'cadence'), the full run is
plausibly safe; if it errors or the values look wrong, that's a much
cheaper failure to debug than hours into the real run.

This is a genuinely long-running job for the full corpus -- likely many
hours, almost certainly longer than a single GitHub Actions job's
runtime cap. Designed to be resumable across separate invocations:
reads whatever's already in the output file and skips those pieces, and
(via --commit-every) commits+pushes its own progress periodically, not
just at the end, so a run that gets killed mid-way doesn't lose
everything before it. Realistically expect to (or have the workflow)
trigger this several times over several days to complete the full
corpus, each run picking up where the last one stopped.

Usage:
    python scripts/precompute_finalis.py [--limit N] [--collection KEY]
        [--out PATH] [--commit-every N]

--limit N: stop after N NEWLY-computed pieces this run (already-done
    pieces from a prior run don't count against it). Also just a handy
    way to page through the corpus a few at a time across repeated runs.
--collection KEY: restrict to one collection (music21/crim/jrp/1520s/
    tasso/seils/lassus_psalms) -- e.g. for a focused test run.
--out PATH: output file (default data/finalis.jsonl, relative to the
    repo root, not this script's own directory).
--commit-every N: git add/commit/push the output file every N newly-
    computed pieces (default 50; 0 disables committing from inside the
    script entirely, e.g. for a local test run with no git remote to
    push to).
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import music21 as m21
import requests
import crim_intervals as ci

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import corpus_sources  # noqa: E402 (needs the sys.path insert above first)


def _import_piece(collection, native_ref):
    """Fetch + import one piece as a crim_intervals ImportedPiece --
    the same per-collection fetch mechanics as app.py's own
    _import_piece_by_collection, duplicated here rather than imported:
    app.py itself isn't safely importable outside a real Streamlit run
    (it calls st.set_page_config()/st.title()/st.caption() at module
    level, not guarded behind `if __name__ == '__main__':`), unlike
    corpus_sources.py's piece-LISTING layer, which was genuinely worth
    sharing (see that module's own docstring) since it has no such
    problem. Returns (piece, error)."""
    if collection == 'music21':
        corpus_key, piece_id = native_ref
        score = m21.corpus.parse(f"{corpus_key}/{piece_id}")
        return ci.main_objs.ImportedPiece(score, piece_id), None
    if collection == 'crim':
        if not native_ref['mei_links']:
            return None, "CRIM has this piece catalogued but no MEI file for it yet."
        piece = ci.importScore(native_ref['mei_links'][0])
        if piece is None:
            return None, "CRIM couldn't import this piece (bad MEI file or network issue)."
        return piece, None
    raw_url = corpus_sources.KERN_COLLECTION_BASE_URLS[collection] + native_ref
    kern_text = requests.get(raw_url, timeout=20).text
    score = m21.converter.parse(kern_text)
    return ci.main_objs.ImportedPiece(score, Path(native_ref).stem), None


_PITCH_CLASS_RE = re.compile(r'^([A-Ga-g][#-]*)')


def _pitch_class(note_name):
    """'G3' -> 'G', 'D#4' -> 'D#' -- strips the trailing octave digit,
    nothing more (music21 already spells flats as '-', not 'b', so no
    extra normalization needed there). None-safe: both cadences()'s
    'Low' column and .final() can genuinely come back None/NaN for a
    piece with no notes/no sounding final moment."""
    if not note_name or not isinstance(note_name, str):
        return None
    m = _PITCH_CLASS_RE.match(note_name)
    return m.group(1) if m else None


def compute_finalis(piece):
    """Returns (finalis_pitch_class_or_None, source, detail).

    source is 'cadence' (the last detected cadence's Low column -- see
    this script's own module docstring for why that's the primary
    method) or 'final_fallback' (piece.final(), used only when zero
    cadences were detected at all). source == 'error' with finalis ==
    None means neither method produced a usable pitch -- recorded
    explicitly, never silently guessed. Never raises: a real crash in
    either crim_intervals call is caught and folded into the returned
    detail string, same discipline as app.py's own _safe_cadences.
    """
    try:
        cadences = piece.cadences(voice_detail=True, include_final=True)
        cadence_error = None
    except Exception as e:
        cadences = None
        cadence_error = f"cadences() failed: {type(e).__name__}: {e}"

    if cadences is not None and not cadences.empty:
        pc = _pitch_class(cadences.iloc[-1]['Low'])
        if pc:
            return pc, 'cadence', None

    try:
        pc = _pitch_class(piece.final())
        if pc:
            return pc, 'final_fallback', cadence_error
    except Exception as e:
        detail = f"final() also failed: {type(e).__name__}: {e}"
        return None, 'error', f"{cadence_error}; {detail}" if cadence_error else detail

    return None, 'error', cadence_error or "cadences() found nothing and final() returned nothing usable"


def load_existing(out_path):
    """{label: record} already written, keyed the same way this script
    writes them, so a resumed run skips pieces already done. Tolerates
    a truncated last line (a hard-killed job can leave one half-written
    line, since json.dumps + '\\n' + flush() per record means at most
    the very last line can ever be partial) by skipping it rather than
    raising -- that one piece just gets recomputed on the next run,
    which is harmless."""
    existing = {}
    if not out_path.exists():
        return existing
    with out_path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            existing[record['label']] = record
    return existing


def git_commit_progress(out_path, done_count, total_count):
    """Commits + pushes the output file so far. Called periodically
    (--commit-every), not just once at the end: this job can run far
    longer than a single CI job's own time limit, so if THIS invocation
    gets killed mid-run, the last periodic commit's progress survives
    for a following manual re-run to resume from (see load_existing).
    Failures here are logged, not fatal -- the previous commit's
    progress is still intact either way, and the next periodic attempt
    (or the final one) just tries again."""
    try:
        subprocess.run(['git', 'add', str(out_path)], check=True)
        subprocess.run(
            ['git', 'commit', '-m', f'Finalis precompute progress: {done_count}/{total_count} pieces'],
            check=True,
        )
        subprocess.run(['git', 'push'], check=True)
        print(f"[commit] pushed progress at {done_count}/{total_count}", flush=True)
    except subprocess.CalledProcessError as e:
        print(f"[commit] FAILED, continuing anyway (progress is still on disk): {e}", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="Batch-compute a heuristic Finalis for every piece in this app's corpus.",
    )
    parser.add_argument('--limit', type=int, default=None,
                         help="Stop after N NEWLY-computed pieces this run.")
    parser.add_argument('--collection', default=None,
                         help="Restrict to one collection key (music21/crim/jrp/1520s/tasso/seils/lassus_psalms).")
    parser.add_argument('--out', default='data/finalis.jsonl',
                         help="Output path, JSON Lines (default: data/finalis.jsonl).")
    parser.add_argument('--commit-every', type=int, default=50,
                         help="git add/commit/push the output every N newly-computed pieces (0 disables).")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_existing(out_path)
    print(f"Resuming: {len(existing)} piece(s) already recorded in {out_path}", flush=True)

    print("Enumerating all collections (corpus_sources.build_browse_index)...", flush=True)
    rows = corpus_sources.build_browse_index()
    if args.collection:
        rows = [r for r in rows if r[1] == args.collection]
    print(f"{len(rows)} piece(s) in scope" + (f" (collection={args.collection})" if args.collection else ""), flush=True)

    todo = [r for r in rows if r[0] not in existing]
    print(f"{len(todo)} piece(s) still to compute.", flush=True)

    done_this_run = 0
    t0 = time.time()
    with out_path.open('a', encoding='utf-8') as f:
        for i, (label, collection, native_ref) in enumerate(todo):
            if args.limit is not None and done_this_run >= args.limit:
                print(f"Hit --limit {args.limit}, stopping this run.", flush=True)
                break

            try:
                piece, error = _import_piece(collection, native_ref)
                if error:
                    record = {'label': label, 'collection': collection, 'finalis': None,
                              'source': 'error', 'detail': error}
                else:
                    finalis, source, detail = compute_finalis(piece)
                    record = {'label': label, 'collection': collection, 'finalis': finalis,
                              'source': source, 'detail': detail}
            except Exception as e:
                record = {'label': label, 'collection': collection, 'finalis': None,
                          'source': 'error', 'detail': f"{type(e).__name__}: {e}"}

            f.write(json.dumps(record, ensure_ascii=False) + '\n')
            f.flush()
            done_this_run += 1

            elapsed = time.time() - t0
            rate = done_this_run / elapsed if elapsed > 0 else 0.0
            print(
                f"[{i + 1}/{len(todo)}] {label[:70]!r} -> {record['finalis']} "
                f"({record['source']}) -- {rate:.2f}/s, {elapsed / 60:.1f}m elapsed",
                flush=True,
            )

            if args.commit_every and done_this_run % args.commit_every == 0:
                git_commit_progress(out_path, len(existing) + done_this_run, len(rows))

    if args.commit_every and done_this_run:
        git_commit_progress(out_path, len(existing) + done_this_run, len(rows))

    print(f"Done. {done_this_run} piece(s) computed this run; "
          f"{len(existing) + done_this_run}/{len(rows)} total recorded.", flush=True)


if __name__ == '__main__':
    main()
