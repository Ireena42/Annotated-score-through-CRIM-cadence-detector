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

Run three times so far, each confirmed against real GitHub state (commit
history, run/job timing) rather than just the green checkmark:
1. A 5-piece test run (correct -- Agnus_00 came back 'G'/'cadence',
   matching this corpus's one hand-verified answer).
2. A full-corpus run that made steady progress (355 pieces in ~6
   minutes) then produced ZERO further progress commits for the
   remaining ~1h47m of a job that still exited "success" -- diagnosed at
   the time as a hang on piece #356 with no per-piece timeout to catch
   it. Added PIECE_TIMEOUT_SECONDS (signal.alarm) as the fix.
3. A second full-corpus run, resuming from #356 with that timeout in
   place, showed the exact same SHAPE of failure (steady progress --
   405 to 1005 -- then silence for the remaining ~1h39m). This time the
   cause was confirmed differently: cross-referencing GitHub's commit
   history showed the repo owner's own machine pushed several unrelated
   commits to master during that exact silent window. git_commit_progress
   had no fetch/rebase before pushing, so the instant any other commit
   landed on master first, its own push became a plain non-fast-forward
   rejection -- and with no recovery logic, EVERY later attempt for the
   rest of the run failed the same way, silently (by design, so a push
   failure wouldn't crash the whole job). The script may well have kept
   computing the whole time; it just could never persist any of it past
   that point. Fixed by having git_commit_progress pull --rebase and
   retry once on a rejected push (see its own docstring) -- this doesn't
   rule out the ORIGINAL per-piece-hang theory being real too (both
   fixes stay in place), but it's a fully sufficient explanation on its
   own for everything observed in run 2, and is the more likely one of
   the two given how precisely the timestamps line up.

This is a genuinely long-running job for the full corpus -- likely many
hours, almost certainly longer than a single GitHub Actions job's
runtime cap, now compounded by whatever pathological pieces the new
timeout will skip past rather than hang on. Designed to be resumable
across separate invocations: reads whatever's already in the output
file and skips those pieces, and (via --commit-every) commits+pushes
its own progress periodically, not just at the end, so a run that gets
killed (or hangs) mid-way doesn't lose everything before it.
Realistically expect to (or have the workflow) trigger this several
times over several days to complete the full corpus, each run picking
up where the last one stopped.

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
import signal
import subprocess
import sys
import time
from pathlib import Path

import music21 as m21
import requests
import crim_intervals as ci

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import corpus_sources  # noqa: E402 (needs the sys.path insert above first)


# Real, observed failure this guards against, not a speculative one:
# the first full-corpus run (2026-08-29) made steady progress (355
# pieces in ~6 minutes) then produced ZERO further progress commits for
# the remaining ~1h47m of a job that still exited with a clean success
# status -- meaning the process didn't crash, it just never returned
# from whatever it was doing on piece #356 (almost certainly a
# pathologically slow piece.cadences() call on some real Palestrina
# piece with an unusual voice/measure count -- CRIM's cadence detector
# is a pairwise-voice search, so an atypical piece could plausibly cost
# far more than the ~1s/piece average seen everywhere else). Nothing in
# the per-piece try/except below catches a HANG, only a raised
# exception -- this signal.alarm-based timeout is what actually bounds
# it. SIGALRM is Unix-only (fine for the GitHub Actions ubuntu-latest
# runner this is designed for) -- guarded so this script doesn't just
# crash on an unsupported platform (e.g. someone running it locally on
# Windows), it just quietly loses the timeout protection there.
PIECE_TIMEOUT_SECONDS = 120  # generous next to the ~1s/piece average seen so far, tight next to "hung for 6000+ seconds"
_HAS_ALARM = hasattr(signal, 'SIGALRM')


class _PieceTimeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _PieceTimeout(f"exceeded the {PIECE_TIMEOUT_SECONDS}s per-piece limit")


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

    Real, observed failure this guards against, not a speculative one:
    the second full-corpus run (2026-08-29) made steady progress (350
    pieces committed in ~15 minutes) then produced ZERO further progress
    commits for the remaining ~1h39m of a job that still exited with a
    clean success status -- looking exactly like the FIRST run's hang,
    but confirmed by a different mechanism this time: cross-referencing
    commit timestamps/authors on GitHub showed the repo owner's own
    local machine pushed several unrelated commits to master during that
    exact window. `git push` with no prior fetch/rebase fails as a plain
    non-fast-forward rejection the moment ANY other commit lands on the
    remote first -- and since this function had no recovery from that,
    EVERY later attempt for the rest of the run kept failing the same
    way (silently caught below, by design, so the job wouldn't crash
    over a push failure) even though the script itself may well have
    kept computing pieces the whole time -- just never able to persist
    them past that point. `git pull --rebase` re-syncs onto whatever
    landed on origin and replays this commit on top before retrying the
    push once; safe here since this job is the only writer to out_path,
    so a real conflict on it would mean two precompute jobs running at
    once, not a routine collision with unrelated work landing on master.
    Failures even after that retry are logged, not fatal -- the local
    commit still exists in this job's own worktree either way, and the
    next periodic attempt (or the final one) tries the whole thing again."""
    try:
        subprocess.run(['git', 'add', str(out_path)], check=True)
        subprocess.run(
            ['git', 'commit', '-m', f'Finalis precompute progress: {done_count}/{total_count} pieces'],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"[commit] commit step FAILED, continuing anyway: {e}", flush=True)
        return

    try:
        subprocess.run(['git', 'push'], check=True)
        print(f"[commit] pushed progress at {done_count}/{total_count}", flush=True)
        return
    except subprocess.CalledProcessError:
        print("[commit] push rejected (probably behind origin -- something else "
              "pushed to master meanwhile) -- pulling --rebase and retrying once", flush=True)

    try:
        subprocess.run(['git', 'pull', '--rebase'], check=True)
        subprocess.run(['git', 'push'], check=True)
        print(f"[commit] pushed progress at {done_count}/{total_count} (after rebase)", flush=True)
    except subprocess.CalledProcessError as e:
        print(f"[commit] FAILED even after rebase, continuing anyway "
              f"(progress is still committed locally in this job's worktree): {e}", flush=True)


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

    if _HAS_ALARM:
        signal.signal(signal.SIGALRM, _timeout_handler)
    else:
        print("Note: signal.SIGALRM isn't available on this platform -- "
              "per-piece timeout protection is disabled here (fine on the "
              "GitHub Actions runner this is designed for; only matters if "
              "run locally on e.g. Windows).", flush=True)

    done_this_run = 0
    t0 = time.time()
    with out_path.open('a', encoding='utf-8') as f:
        for i, (label, collection, native_ref) in enumerate(todo):
            if args.limit is not None and done_this_run >= args.limit:
                print(f"Hit --limit {args.limit}, stopping this run.", flush=True)
                break

            if _HAS_ALARM:
                signal.alarm(PIECE_TIMEOUT_SECONDS)
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
                # Catches _PieceTimeout too (it's a plain Exception
                # subclass) -- its own message already says "exceeded
                # the Ns per-piece limit", clear enough without a
                # separate except clause just for it.
                record = {'label': label, 'collection': collection, 'finalis': None,
                          'source': 'error', 'detail': f"{type(e).__name__}: {e}"}
            finally:
                if _HAS_ALARM:
                    signal.alarm(0)  # cancel -- otherwise a fast piece right after a near-timeout could get a stray alarm mid-flight

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
