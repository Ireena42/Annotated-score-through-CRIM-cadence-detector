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
Finalis (`source`: 'cadence' or 'final_fallback'), 'part_duration_mismatch'
when the piece's own encoded voice-parts don't all reach the same total
duration (see _parts_desynced -- found 2026-08-30 when a user spot-checked
a real result and caught it wrong), or records an `error` with no guess
at all rather than a silently wrong one.

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
4. The full corpus finished (2026-08-30), but a user spot-checking a
   real result caught Monteverdi's "O Mirtillo, Mirtill' Anima Mia"
   recorded as finalis E when the piece actually ends on D. Traced
   directly (not guessed) to a real data-quality defect: this specific
   music21-corpus file has desynchronized voice-parts -- several
   voices' own encoded content runs out tens of measures before the
   piece's true end, so both the cadence detector and .final() end up
   reading a texture silently missing most of its real voices for the
   whole closing passage. Sampling 6 Monteverdi pieces found this in 2
   of them -- a real, recurring defect, not a one-off. Fixed by adding
   _parts_desynced as a cheap pre-check; affected pieces still get a
   best-guess finalis, but tagged low-confidence (source =
   'part_duration_mismatch') rather than presented at face value. A
   fresh full-corpus recompute with this fix is needed to know how many
   other pieces were silently affected the same way.

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
import math
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
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
    # Same retry-on-transient-failure helper corpus_sources.py's own
    # enumeration fetches now use (see its docstring for the real
    # incident this guards against) -- a per-piece fetch failing here
    # only costs one piece (caught by main()'s own per-piece try/except,
    # recorded as an 'error' record, not a crashed run), but a transient
    # blip shouldn't cost even that when a cheap retry would avoid it.
    kern_text = corpus_sources._get_with_retry(raw_url, timeout=20).text
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


# One measure's worth of quarter notes in common time -- generous
# enough that a real anacrusis/pickup-measure difference between parts
# never trips this, but nowhere near the tens-of-quarter-notes spread
# actually observed in the broken pieces this guards against (see
# _parts_desynced's own docstring).
PART_DESYNC_TOLERANCE = 4.0


def _parts_desynced(piece):
    """True if this piece's own encoded voice-parts don't all reach the
    same total duration -- a real, confirmed data-quality defect in a
    meaningful slice of this corpus (sampled 6 Monteverdi pieces: 2
    showed it), not a compositional device. Concretely found on
    "O Mirtillo, Mirtill' Anima Mia" (monteverdi/madrigal.5.2): Canto
    and Continuo's own encoded content runs out at offset 276 and
    Quinto's at 288, while the piece's real ending (confirmed by ear,
    and matching piece.final()) is at offset 340 -- so for the whole
    final ~16 measures, several voices simply aren't present in the
    data at all (not resting -- their streams have no more content,
    notes or rests, past that point). Both the cadence detector (which
    needs a real complementary voice-pair to recognize a cadential
    pattern) and piece.final() end up reading a texture silently
    missing most of its real voices -- neither method's answer should
    be trusted at face value when this is true.
    """
    parts = piece.score.parts
    if len(parts) < 2:
        return False
    times = [p.highestTime for p in parts]
    return (max(times) - min(times)) > PART_DESYNC_TOLERANCE


def _signals_for_piece(piece):
    """Computes the three independent finalis signals compute_finalis()
    cross-checks, each None-safe on its own (a crash/NaN in one signal
    never prevents the other two from still being computed):

    - 'low': the last detected cadence's Low column (lowest sounding
      pitch at the cadential arrival) -- the original "clausula
      basizans" method this whole heuristic started from.
    - 'tone': that SAME cadence's Tone column (the Cantizans/Altizans's
      own goal note) -- usually equal to Low, but not when the
      Bassizans is evaded: confirmed directly on Palestrina's Gloria_42
      (finalis_findings.md #3 in this repo's own investigation), where
      Low reported the bass's actual (wrong) note while Tone stayed
      correct, and CadType never flagged the cadence as a whole as
      Evaded/Abandoned since the OTHER two roles were fully realized.
    - 'final': crim_intervals' own piece.final() (literal last note,
      no cadence detection at all) -- the "cruder" method this
      heuristic originally used only as a last resort, but which won
      9 of 10 hand-verified cases specifically where Low had already
      gone wrong (finalis_findings.md #5).

    Returns ({'low':.., 'tone':.., 'final':..}, cadence_error_or_None).
    """
    signals = {'low': None, 'tone': None, 'final': None}
    cadence_error = None
    try:
        cadences = piece.cadences(voice_detail=True, include_final=True)
        if cadences is not None and not cadences.empty:
            last = cadences.iloc[-1]
            signals['low'] = _pitch_class(last.get('Low'))
            tone_val = last.get('Tone')
            if tone_val is not None and not (isinstance(tone_val, float) and math.isnan(tone_val)):
                signals['tone'] = _pitch_class(tone_val)
    except Exception as e:
        cadence_error = f"cadences() failed: {type(e).__name__}: {e}"

    try:
        signals['final'] = _pitch_class(piece.final())
    except Exception:
        pass

    return signals, cadence_error


def compute_finalis(piece):
    """Returns (finalis_pitch_class_or_None, source, detail).

    Redesigned 2026-09-01 as a genuine multi-signal cross-check (see
    finalis_findings.md in this repo for the full investigation this is
    based on) rather than trusting one value with a single fallback.
    Computes all three _signals_for_piece() values and only calls the
    result confident when at least two INDEPENDENT signals agree --
    deliberately not "always prefer piece.final()", which would just
    repeat the original design's mistake in the other direction; see
    finalis_findings.md's own "Open questions" for why that was
    rejected. This directly targets three confirmed failure modes of
    the original Low-only design:
    - an evaded Bassizans corrupting Low even when the cadence looks
      fully resolved (#3 in finalis_findings.md);
    - Low/Tone both wrong on a desynced/truncated encoding where
      piece.final() turned out more reliable (#2);
    - Low and Tone are NOT actually independent evidence when they
      happen to agree with each other -- both come from the SAME
      detected cadence, so "Low == Tone" is really one opinion (the
      cadence detector's), not two. A genuinely random 24-piece
      validation sample (#9 in finalis_findings.md) found 3 real cases
      where Low == Tone but BOTH were wrong, together, because the
      cadence they came from wasn't actually the piece's true final one
      (undetected tail, or an evaded gesture near the true end) --
      the old design counted that as "2 of 3 agree" and called it
      confident anyway, which was wrong all 3 times. Confirmed this
      isn't fixable with a numeric threshold on the cadence's own
      ToNext/Progress columns either: 7 separate CORRECTLY-confident
      pieces in that same sample share the exact ToNext=16 value as one
      of the 3 misses -- no cutoff can separate them, since the values
      are identical. The fix below needs no threshold at all: it's
      categorical (do the AVAILABLE signals include a second,
      genuinely independent one, not just a second column).

    source values, most to least trustworthy:
    - 'confident_unanimous': all 3 signals were available and agree.
    - 'confident_majority': Low and Tone do NOT count as two
      independent votes when they're equal (see above) -- this tier
      requires either (a) Low != Tone and any 2 of the (up to 3)
      available signals agree, or (b) Low == Tone and piece.final() is
      unavailable to cross-check against at all (so there's genuinely
      only one opinion on offer, but nothing else to contradict it).
    - 'low_confidence_split': Low == Tone (the cadence detector's own
      consistent answer) but piece.final() is available and disagrees
      -- exactly the pattern #9 found being silently miscounted as
      confident before this fix. Also covers the older case: 2+
      signals available but none agree with each other at all. Finalis
      is still recorded (Low's own value, for continuity) but flagged,
      not presented as trustworthy.
    - 'single_signal': only ONE signal could be computed at all -- no
      cross-check was actually possible, so this is NOT the same
      confidence tier as an agreeing pair even though it's the only
      value on offer.
    - 'part_duration_mismatch': overrides any of the above when
      _parts_desynced is True -- an encoding-integrity problem that can
      corrupt any/all three signals simultaneously, so agreement
      between them proves nothing when this is true.
    - 'error': none of the three signals produced a usable pitch class.

    detail always records the raw per-signal values (e.g.
    "signals: {'low': 'E', 'tone': 'C', 'final': 'C'}"), so a
    'low_confidence_split'/'single_signal' record stays inspectable,
    not just a bare flag. Never raises: a crash in any signal, or in
    _parts_desynced itself, is caught and folded into detail, same
    discipline as app.py's own _safe_cadences.
    """
    signals, cadence_error = _signals_for_piece(piece)
    low, tone, final = signals.get('low'), signals.get('tone'), signals.get('final')
    available = {k: v for k, v in signals.items() if v}
    n_available = len(available)

    if not available:
        return None, 'error', cadence_error or "no signal produced a usable pitch class"

    if n_available == 1:
        source = 'single_signal'
        top_value = next(iter(available.values()))
    elif low is not None and tone is not None and low == tone:
        # Low and Tone are the SAME opinion (the cadence detector's own),
        # not two independent ones -- only piece.final() being available
        # and either agreeing or disagreeing actually changes confidence
        # here (see this function's own docstring for why, and #9 in
        # finalis_findings.md for the real cases this was built from).
        if final is None:
            source = 'confident_majority'
            top_value = low
        elif final == low:
            source = 'confident_unanimous'
            top_value = low
        else:
            source = 'low_confidence_split'
            top_value = low  # keep the cadence detector's own answer as the recorded guess, for continuity
    else:
        # Low and Tone disagree (or one of them is unavailable) -- any
        # two of the available signals agreeing here IS genuine
        # independent agreement, not the correlated-pair problem above.
        counts = Counter(available.values())
        top_value, top_count = counts.most_common(1)[0]
        if top_count == n_available:
            source = 'confident_unanimous'
        elif top_count >= 2:
            source = 'confident_majority'
        else:
            # n_available == 3 and all three disagree (top_count == 1) --
            # the only way to reach this branch.
            source = 'low_confidence_split'
            top_value = low if low is not None else top_value

    detail = f"signals: {signals}" + (f"; {cadence_error}" if cadence_error else "")

    try:
        desynced = _parts_desynced(piece)
    except Exception as e:
        desynced = False
        detail += f"; _parts_desynced check itself failed: {type(e).__name__}: {e}"

    if desynced:
        detail = f"low-confidence: parts' own encoded durations disagree (would otherwise be {source!r} -> {top_value}); {detail}"
        return top_value, 'part_duration_mismatch', detail

    return top_value, source, detail


# _movement_group_key moved to corpus_sources.py (canonical definition,
# used there now by group_browse_rows()/parse_music21_piece() too --
# imported here rather than kept as a second, driftable copy). Why this
# exists at all: Palestrina's long Gloria/Credo settings are often split
# into several files, one per liturgical text clause (e.g. Gloria_14_a=
# "First Section" through Gloria_14_i="Amen") -- but all parts of one
# such movement carry the IDENTICAL humdrum:RNB metadata value, meaning
# the source encoding itself treats finalis as a whole-movement
# property, not something each part independently arrives at
# (finalis_findings.md #6). Computing Finalis separately per part meant
# measuring an internal sectional cadence for every part except the
# last, not the movement's actual ending.
_movement_group_key = corpus_sources._movement_group_key


def _group_rows_for_finalis(rows):
    """Groups rows into finalis-computation units. A multi-part music21
    movement (see _movement_group_key) becomes one group whose members
    share ONE computed finalis, taken from the group's LAST part (by
    part-letter order -- 'a' < 'b' < ... < 'i' -- assumed to also be
    liturgical/chronological order within the movement; confirmed
    directly for one Gloria via each part's own OTL text-incipit field:
    "First Section", "Benedicimus te", ..., "Amen", in exactly that
    order). Every other row (non-music21 collections, or a music21
    piece with no part suffix) is its own group of one.

    Returns a list of groups, each a list of (label, collection,
    native_ref) tuples already sorted so group[-1] is the piece to
    actually import and compute on.
    """
    # Plain dict, not defaultdict: insertion order (Python 3.7+ guarantees
    # this) is what keeps `result` below in the same overall order `rows`
    # came in, without needing a separate order-tracking list.
    groups = {}
    for row in rows:
        label, collection, native_ref = row
        if collection == 'music21':
            corpus_key, piece_id = native_ref
            group_key, part_letter = _movement_group_key(piece_id)
            full_key = (collection, corpus_key, group_key)
        else:
            full_key = (collection, label)  # every non-music21 row is its own group
            part_letter = None
        groups.setdefault(full_key, []).append((row, part_letter or ''))

    result = []
    for members in groups.values():
        members.sort(key=lambda m: m[1])  # '' (no suffix) sorts before any letter
        result.append([m[0] for m in members])
    return result


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
                         help="Stop after N NEWLY-computed piece-records this run. "
                              "Approximate, not exact: a multi-part movement's records "
                              "are all written together once its group is computed, so "
                              "a run can overshoot this by up to one group's size.")
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

    # Groups, not flat rows: a multi-part music21 movement (see
    # _movement_group_key) is computed ONCE, from its last part, and that
    # one result is shared across every member -- see _group_rows_for_finalis's
    # own docstring for why. A group where every member is already in
    # `existing` needs no import/compute at all; a group whose LAST member
    # is already recorded (but earlier members aren't -- a resumed run that
    # got partway through a group before stopping) reuses that recorded
    # result instead of re-importing/re-computing it from scratch.
    groups = _group_rows_for_finalis(rows)
    todo_groups = [g for g in groups if any(m[0] not in existing for m in g)]
    todo_member_count = sum(1 for g in todo_groups for m in g if m[0] not in existing)
    print(f"{len(todo_groups)} group(s) ({todo_member_count} piece-record(s)) still to compute.", flush=True)

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
        for i, group in enumerate(todo_groups):
            if args.limit is not None and done_this_run >= args.limit:
                print(f"Hit --limit {args.limit}, stopping this run.", flush=True)
                break

            last_label, last_collection, last_native_ref = group[-1]

            if last_label in existing:
                # Last part already recorded from a prior run -- reuse it
                # rather than re-importing/re-computing.
                last_record = existing[last_label]
                finalis, source, base_detail = last_record['finalis'], last_record['source'], last_record['detail']
                group_error = None
            else:
                if _HAS_ALARM:
                    signal.alarm(PIECE_TIMEOUT_SECONDS)
                try:
                    piece, group_error = _import_piece(last_collection, last_native_ref)
                    if group_error:
                        finalis, source, base_detail = None, 'error', group_error
                    else:
                        finalis, source, base_detail = compute_finalis(piece)
                except Exception as e:
                    # Catches _PieceTimeout too (a plain Exception subclass)
                    # -- its own message already says "exceeded the Ns
                    # per-piece limit", clear enough without a separate
                    # except clause just for it.
                    finalis, source, base_detail = None, 'error', f"{type(e).__name__}: {e}"
                    group_error = base_detail
                finally:
                    if _HAS_ALARM:
                        signal.alarm(0)  # cancel -- otherwise a fast piece right after a near-timeout could get a stray alarm mid-flight

            n_written_this_group = 0
            for label, collection, native_ref in group:
                if label in existing:
                    continue
                if label == last_label:
                    detail = base_detail
                else:
                    # An earlier part of the same multi-part movement --
                    # this label's OWN last cadence was never even looked
                    # at; the finalis is the movement's, computed from its
                    # last part (see _group_rows_for_finalis).
                    detail = f"finalis inherited from movement's last part ({last_label}): {base_detail}"
                record = {'label': label, 'collection': collection, 'finalis': finalis,
                          'source': source, 'detail': detail}
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
                done_this_run += 1
                n_written_this_group += 1

            f.flush()
            elapsed = time.time() - t0
            rate = done_this_run / elapsed if elapsed > 0 else 0.0
            group_note = f" (+{n_written_this_group - 1} inherited)" if n_written_this_group > 1 else ""
            print(
                f"[{i + 1}/{len(todo_groups)}] {last_label[:70]!r} -> {finalis} "
                f"({source}){group_note} -- {rate:.2f}/s, {elapsed / 60:.1f}m elapsed",
                flush=True,
            )

            if args.commit_every and done_this_run % args.commit_every == 0:
                git_commit_progress(out_path, len(existing) + done_this_run, len(rows))

    if args.commit_every and done_this_run:
        git_commit_progress(out_path, len(existing) + done_this_run, len(rows))

    total_recorded = len(existing) + done_this_run
    print(f"Done. {done_this_run} piece(s) computed this run; "
          f"{total_recorded}/{len(rows)} total recorded.", flush=True)

    # Lets the calling workflow decide whether to trigger another run
    # without re-fetching/re-counting anything itself (build_browse_index()
    # is a real, network-dependent call across all 7 collections -- this
    # avoids the workflow paying that cost a second time just to check).
    # $GITHUB_OUTPUT is a GitHub Actions-only file path (unset when run
    # locally or anywhere else), so this is a no-op outside CI.
    github_output = os.environ.get('GITHUB_OUTPUT')
    if github_output:
        with open(github_output, 'a', encoding='utf-8') as f:
            f.write(f"total_recorded={total_recorded}\n")
            f.write(f"total_rows={len(rows)}\n")
            f.write(f"complete={'true' if total_recorded >= len(rows) else 'false'}\n")


if __name__ == '__main__':
    main()
