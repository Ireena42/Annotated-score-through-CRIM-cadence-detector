"""
RENAISSANCE POLYPHONY RESEARCH TOOLKIT -- a small Streamlit web app that
aggregates ~4,300 pieces across 7 Renaissance-polyphony sources (a
music21-bundled corpus, CRIM Project, Josquin Research Project, 1520s
Project, Tasso in Music Project, SEILS, Lassus's Geistliche Psalmen)
into one browsable, searchable place, then runs CRIM Intervals'
structural analyses (cadences, points of imitation, homorhythm) and
writes the results back onto the score itself, plots them across the
piece, or exports them as data. Started as a single-purpose "Cadence
Annotator" (see git history/earlier commit messages for that phase),
then "Renaissance Score Workbench" once the collection-aggregation side
grew into something worth naming on its own; renamed again once cadence
annotation stopped being the single headline feature among several.

Everything below runs in a single process, in the `crim` conda env (same
env that runs crim_export_cadences.py / annotate_cadences.py on the
command line -- see those files' docstrings for why one env is enough
here). Streamlit re-runs this whole script top-to-bottom on every user
interaction (button click, dropdown change, etc.) -- that's normal for
Streamlit, not a bug; it's why there's no explicit event-loop code below.

"""
import csv
import html
import io
import re
import sys
import zipfile
from pathlib import Path
from tempfile import NamedTemporaryFile

import music21 as m21
import pandas as pd
import requests
import streamlit as st

# so `from annotate_cadences import ...` finds the file regardless of the
# directory `streamlit run` was launched from
sys.path.insert(0, str(Path(__file__).parent))
from annotate_cadences import (
    annotate_score, annotate_presentation_types, annotate_homorhythm,
    CADENCE_COLOR, PRESENTATION_COLOR, HOMORHYTHM_COLOR,
)
from crim_export_cadences import export_cadences_with_partmap  # noqa: F401 (kept for reference)
import crim_intervals as ci

st.set_page_config(page_title="Renaissance Polyphony Research Toolkit", layout="centered")
st.title("Renaissance Polyphony Research Toolkit")
st.caption(
    "Renaissance polyphony is scattered across dozens of separate archives "
    "online -- this app gathers ~4,300 pieces from 7 of them into one "
    "searchable, analysis-ready place. Run CRIM's structural analyses "
    "(cadences, points of imitation, homorhythmic passages), see where they "
    "fall across the piece, and take the results further -- an annotated "
    "score for MuseScore/Finale, a raw file for your own code, or a dataset "
    "across a whole search."
)


# Every music21-bundled composer checked before landing on this set (see
# conversation): 'josquin' and 'ciconia' both parse fine but come out as
# single-part scores (josquin's ABC files are monophonic transcriptions
# despite titles literally saying "4v."; ciconia's one piece has no nested
# voices either) -- CRIM's cadence detector needs >=2 real contrapuntal
# voices and returns nothing for a 1-part score, so those two would just
# always report "no cadences detected." 'trecento' is a mixed-composer
# collection (various, mostly anonymous), not a single composer, so it's
# left out of this specific selector. 'lusitano'/'luca' were tried and
# dropped -- one piece each, not worth a permanent slot. CRIM's own corpus
# already spans both sacred and secular Renaissance repertoire, so no
# sacred/secular caveat is needed for monteverdi (secular madrigals) below
# either -- it's simply more of what's already on offer via the CRIM tab.
CORPUS_COMPOSERS = {
    'Palestrina': 'palestrina',
    'Monteverdi': 'monteverdi',
}

_ROMAN_NUMERALS = {'I', 'II', 'III', 'IV', 'V', 'VI'}


@st.cache_resource(show_spinner=False)
def _metadata_bundle():
    """music21's pre-built metadata index for the whole bundled corpus --
    lets every piece's title/composer/etc. be read WITHOUT parsing each
    full score (parsing all 1318 Palestrina files just to build a piece
    list would take minutes; querying the bundle is ~instant per piece,
    timed directly before writing this). st.cache_resource (not
    cache_data) because this holds a live, non-trivially-picklable index
    object meant to be reused as-is across reruns, not serialized."""
    return m21.corpus.corpora.CoreCorpus().metadataBundle


def _generic_piece_label(md, stem):
    """Title for a non-Palestrina piece (currently just monteverdi). Not
    just `md.title or stem` -- checked directly and found 5 of
    monteverdi's 53 entries have no `title` at all, so that fallback was
    silently showing the raw internal stem (e.g. 'madrigal.5.7') as the
    label. 4 of those 5 actually have the real title sitting in
    `movementName` instead (e.g. '3.12: Perfidissimo Volto' -- just needs
    its leading catalogue-number prefix stripped); only 1 of the 53
    ('madrigal.5.7') has no real title anywhere in the file's own
    metadata at all -- that's a genuine gap in the source data, not
    something recoverable here, so it falls through to a humanized
    version of the stem as a last resort ('Madrigal 5.7')."""
    if md.title:
        return md.title
    mv = md.movementName
    if mv and mv not in (stem, Path(md.sourcePath).name):
        return re.sub(r'^\d+(\.\d+)?[:.]\s*', '', mv).strip()
    parts = stem.split('.')
    return f'{parts[0].capitalize()} {".".join(parts[1:])}' if len(parts) > 1 else stem.replace('_', ' ').title()


def _palestrina_movement_label(stem):
    """Turns a stem like 'Agnus_I_30' or 'Credo_29_a' into 'Agnus I' /
    'Credo (part a)' -- NOT built from the metadata bundle's own 'title'
    field, because that field is unreliable for a real chunk of this
    corpus: pieces split into lettered sub-files (fragments across a
    texture/meter change, e.g. the '_a'/'_b'/'_c' suffixes mentioned in
    this project's own todo.md) come back with title='First Section' --
    a generic placeholder that loses the actual movement identity
    entirely (confirmed directly: e.g. 'Missa Ascendo ad Patrem'/'First
    Section' covers a mix of real Agnus/Benedictus/Credo/Sanctus files).
    The file stem's own prefix convention (genre name first) is the more
    reliable source of truth here -- it's also literally what
    CLAUDE.md's own metadata pattern already treats as the real piece ID.
    """
    tokens = stem.split('_')
    genre = tokens[0]
    rest = tokens[1:]
    numeral = f' {rest[0]}' if rest and rest[0] in _ROMAN_NUMERALS else ''
    if numeral:
        rest = rest[1:]
    part = f' (part {rest[-1]})' if rest and len(rest[-1]) == 1 and rest[-1].isalpha() else ''
    return f'{genre}{numeral}{part}'


@st.cache_data(show_spinner=False)
def list_pieces_for_composer(corpus_key):
    """{label: piece_id} for one composer, e.g. 'Missa De Beata Marie
    Virginis (II): Agnus' -> 'Agnus_00' for palestrina -- same naming
    spirit as the CRIM tab's own dropdown. Cached (st.cache_data) so this
    -- cheap once the bundle is loaded, but still real work across 1300+
    pieces -- only runs once per composer per app process.

    Palestrina gets 'Mass title: Movement' (see _palestrina_movement_label
    for why movement isn't taken from raw metadata). Other composers (just
    monteverdi for now) get the piece's own 'title' directly -- there's no
    mass grouping for standalone madrigals, so nothing to build on top of.

    Label collisions are real and checked for, not assumed away: 23 of
    1318 Palestrina pieces still collide even with the scheme above (e.g.
    two entirely different pieces both catalogued as 'Missa Veni Sancte
    Spiritus') -- genuine same-title-different-piece cases, not a labeling
    bug. Any label that would otherwise map to more than one piece id gets
    that id appended in brackets, so nothing becomes unreachable in the
    dropdown -- but only those rare cases, so the other ~98% stay clean.
    """
    bundle = _metadata_bundle()
    entries = bundle.search(corpus_key, field='composer')

    raw_labels = {}  # label -> list of piece ids sharing it
    for entry in entries:
        md = entry.metadata
        # sourcePath, not corpusFilePath: checked directly against BOTH
        # music21 versions in play across this project's two envs --
        # corpusFilePath doesn't exist at all on 8.3.0 (the crim env this
        # app actually runs in -- confirmed by AttributeError, not assumed)
        # while sourcePath holds the same relative-path value on both.
        if not md.sourcePath:
            continue
        stem = Path(md.sourcePath).stem
        if corpus_key == 'palestrina':
            label = f'{md.parentTitle}: {_palestrina_movement_label(stem)}'
        else:
            label = _generic_piece_label(md, stem)
        raw_labels.setdefault(label, [])
        if stem not in raw_labels[label]:
            raw_labels[label].append(stem)

    return _dedupe_labels(raw_labels)


def _dedupe_labels(raw_labels):
    """{label: [ids sharing it]} -> {label: id}, appending the id itself
    in brackets for any label mapping to more than one id, so nothing
    becomes silently unreachable in a dropdown. Shared by every
    collection below (music21 corpus, JRP, 1520s, Tasso, ...) -- each
    one hits real, checked collisions (documented at each call site),
    not a hypothetical worth guarding against speculatively."""
    result = {}
    for label, ids in raw_labels.items():
        if len(ids) == 1:
            result[label] = ids[0]
        else:
            for id_ in ids:
                result[f'{label} [{Path(id_).stem}]'] = id_
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_crim_pieces():
    """The CRIM Project's full piece list, live from their public API --
    verified directly (curl) before writing this: 359 pieces, 48 composers,
    each entry carrying piece_id/title/composer/genre/mei_links. Cached for
    1 hour (ttl=3600, same value intervals_streamlit2.py uses for the same
    call) so a page full of users doesn't refetch this on every rerun --
    Streamlit reruns the whole script on every interaction (see module
    docstring), so without caching this would hit crimproject.org on every
    single button click, dropdown change, etc.
    """
    response = requests.get('https://crimproject.org/data/pieces/', timeout=15)
    response.raise_for_status()
    return response.json()


# Composer names for the Josquin Research Project (jrp-scores repo), read
# directly from each composer folder's own file headers (!!!COM: lines in
# a real, non-redirect .krn file) rather than trusted from the repo's own
# index.hmd -- that index turned out to be stale (its file paths include a
# '/kern/' subdirectory that doesn't exist in the actual current repo, and
# it even uses a different code for Ockeghem, 'Ock' vs the real 'Oke').
# 'Gas' is the one deliberate override: its own first real file happens to
# be an anonymous-attribution piece, but the folder as a whole is
# Gaspar van Weerbeke's per JRP's own about page. 'Mou' and 'binx' are
# left out entirely -- checked directly and every single file under Mou/
# is a tiny redirect stub (<45 bytes, a relative path to a file actually
# stored under another composer's folder), so there's no real content to
# offer under that code at all; binx/ is the repo's own build scripts.
JRP_COMPOSER_NAMES = {
    'Agr': 'Agricola, Alexander', 'Ano': 'Anonymous', 'Bin': 'Binchois, Gilles',
    'Bru': 'Brumel, Antoine', 'Bus': 'Busnoys, Antoine', 'Com': 'Compere, Loyset',
    'Das': 'Daser, Ludwig', 'Duf': 'Du Fay, Guillaume', 'Fry': 'Frye, Walter',
    'Fva': 'Févin, Antoine de', 'Gas': 'Gaspar van Weerbeke', 'Isa': 'Isaac, Heinrich',
    'Jap': 'Japart, Jean', 'Jos': 'Josquin des Prez', 'Mar': 'Martini, Johannes',
    'Obr': 'Obrecht, Jacob', 'Oke': 'Okeghem, Johannes', 'Ort': 'de Orto, Marbrianus',
    'Pip': 'Pipelare, Matthaeus', 'Reg': 'Regis, Johannes', 'Rue': 'la Rue, Pierre de',
    'Tin': 'Tinctoris, Johannes',
}


def _catalog_piece_label(composer_name, stem):
    """'Agr1001a-Missa_In_myne_zin-Gloria' -> 'Agricola, Alexander --
    Missa In myne zin: Gloria'. The catalogue-number prefix (composer/
    project code + work number + optional movement letter) is stripped;
    whatever follows is underscore-to-space-converted and, if there's a
    further '-'-separated movement/source segment, joined on ': ' the
    same way the Palestrina tab formats mass:movement. Shared by JRP and
    the 1520s Project -- both use this exact filename convention,
    confirmed directly against real filenames from each before reusing
    the same function rather than assuming they'd match."""
    rest = re.sub(r'^[A-Za-z]+\d+[a-z]?-', '', stem)
    segments = [html.unescape(s.replace('_', ' ')) for s in rest.split('-')]
    title = segments[0]
    piece_desc = f'{title}: {" ".join(segments[1:])}' if len(segments) > 1 else title
    return f'{composer_name} — {piece_desc}'


def _fetch_catalog_collection(owner, repo, branch, composer_names, min_size=500):
    """Generic fetcher for JRP-style repos: composer/project-code
    top-level folders, filenames like '<Code><Num><Letter?>-<Title>
    [-<Movement>].krn', read via one recursive git-tree API call.
    Shared by JRP and the 1520s Project (structurally identical, checked
    directly for each before merging them into one function).

    min_size filters out redirect-stub files (a relative-path text blob
    standing in for a piece actually stored under another composer's
    folder, e.g. JRP's entire Mou/ folder) -- 500 bytes is a wide,
    verified margin: real scores in both repos run from ~1KB to tens of
    KB, every redirect stub found was under 60 bytes.
    """
    response = requests.get(
        f'https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}',
        params={'recursive': '1'}, timeout=20,
    )
    response.raise_for_status()
    tree = response.json().get('tree', [])

    raw_labels = {}
    for item in tree:
        path = item['path']
        if not path.endswith('.krn') or item.get('size', 0) < min_size:
            continue
        # immediate parent folder, not path.split('/')[0]: JRP's layout is
        # flat ('<Code>/<file>.krn') but 1520s nests one level deeper
        # ('humdrum/<Code>/<file>.krn') -- confirmed directly against both
        # repos' real trees before writing this, not assumed identical.
        code = path.split('/')[-2]
        if code not in composer_names:
            continue
        label = _catalog_piece_label(composer_names[code], Path(path).stem)
        raw_labels.setdefault(label, [])
        if path not in raw_labels[label]:
            raw_labels[label].append(path)

    return _dedupe_labels(raw_labels)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_jrp_pieces():
    """{label: repo path} for the Josquin Research Project's full corpus,
    live from GitHub's tree API -- one request lists the ENTIRE repo
    (1427 entries) at once, verified directly, rather than one request
    per composer folder (would be 20+ separate calls against GitHub's
    unauthenticated rate limit for no real benefit)."""
    return _fetch_catalog_collection(
        'josquin-research-project', 'jrp-scores', 'main', JRP_COMPOSER_NAMES
    )


# Composer names for The 1520s Project (1520s-project-scores repo), read
# directly from the repo's own README table (a proper HTML table mapping
# code -> name -- much more reliable than JRP's, which had to be
# reconstructed from individual file headers since no such table existed
# there). Verified every code in the actual repo tree is covered by this
# table (checked directly, zero orphans) before using it.
PROJECT_1520S_COMPOSER_NAMES = {
    'Any': 'Anonymous', 'Arc': 'Jacques Arcadelt', 'Bar': 'Hotinet Barra',
    'Bau': 'Noel Bauldeweyn', 'Bis': 'Bisgueria', 'Bnt': 'Johannes Brunet',
    'Boy': 'Boyleau', 'Cha': 'Nicolas Champion', 'Con': 'Jean Conseil',
    'Crp': 'Carpentras', 'Div': 'Antonius Divitis', 'Era': 'Erasmus?',
    'Fsc': 'Costanzo Festa', 'Fss': 'Sebastiano Festa', 'Fva': 'Antoine de Févin',
    'Gom': 'Nicolas Gombert', 'Gsc': 'Mathieu Gascongne', 'Jac': 'Jacotin Frontin?',
    'Jan': 'Maistre Jan', 'Jom': 'Jacquet of Mantua', 'Lfg': 'Jean de la Fage',
    'Lhe': 'Jean Lhéritier', 'Lpi': 'Johannes Lupi', 'Lps': 'Lupus Hellinck',
    'Lsa': 'Jean Le Santier', 'Mlu': 'Pierre Moulu', 'Mou': 'Jean Mouton',
    'Opi': 'Benedictus de Opitiis', 'Ren': 'Renaldo', 'Res': 'Nicole Regnes',
    'Ric': 'Jean Richafort', 'Ror': 'Cipriano de Rore', 'Ser': 'Claudin de Sermisy',
    'Snf': 'Ludwig Senfl', 'Sil': 'Andreas de Silva', 'The': 'Pierrequin de Therache',
    'Ver': 'Philippe Verdelot', 'Vin': 'Jheronimus Vinders', 'Wil': 'Adrian Willaert',
}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_1520s_pieces():
    """{label: repo path} for The 1520s Project (ca. 1510-1540 music,
    mostly France/Germany/Italy/Low Countries) -- 662 real pieces,
    verified directly against the repo's file tree."""
    return _fetch_catalog_collection(
        'benory', '1520s-project-scores', 'main', PROJECT_1520S_COMPOSER_NAMES
    )


def _tasso_piece_label(stem):
    """'Tam1010001a-Vorrai_dunque_pur_Silvia--Porto_1625' -> 'Porto
    (1625) -- Vorrai dunque pur Silvia'. The Tasso in Music Project
    names files '<catalog-code>-<poem title>--<composer>_<year>' (the
    catalog code prefix encodes the specific Tasso poem via the Solerti
    numbering, not the composer -- confirmed directly against the
    project's own README, which is why this needs its own parser
    instead of reusing _catalog_piece_label). Checked the real data
    before trusting this shape: 498 of 503 files match it exactly; the
    remaining 5 either have no composer suffix at all, or use a single
    '-' instead of '--' -- both handled by the fallback regex below
    rather than assumed not to exist.
    """
    rest = re.sub(r'^T\w{2}\d+[a-z]?-', '', stem)
    if '--' in rest:
        title_part, _, composer_part = rest.partition('--')
    else:
        # fallback for the ~1% of files without a clean '--' separator
        m = re.match(r'^(.*)-([A-Za-z]+_\d{4})$', rest)
        title_part, composer_part = (m.group(1), m.group(2)) if m else (rest, '')
    title = html.unescape(title_part.replace('_', ' '))
    m = re.match(r'^(.+?)_(\d{4})$', composer_part)
    composer = f'{m.group(1)} ({m.group(2)})' if m else (composer_part.replace('_', ' ') or 'Unknown composer')
    return f'{composer} — {title}'


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_tasso_pieces():
    """{label: repo path} for the Tasso in Music Project (madrigal
    settings of Torquato Tasso's poetry, mostly 1570s-1640s, many
    composers) -- 503 real pieces across 8 genre-code folders (Tam, Tbv,
    Tco, Tec, Tri, Trm, Trt, Tsg -- different Tasso poem collections, not
    composers; composer comes from the filename itself, see
    _tasso_piece_label). No composer-code allowlist needed here (unlike
    JRP/1520s) since every genre folder is real content, verified
    directly (zero redirect-stub-sized files in the whole repo)."""
    response = requests.get(
        'https://api.github.com/repos/TassoInMusicProject/tasso-scores/git/trees/master',
        params={'recursive': '1'}, timeout=20,
    )
    response.raise_for_status()
    tree = response.json().get('tree', [])

    raw_labels = {}
    for item in tree:
        path = item['path']
        if not path.endswith('.krn'):
            continue
        label = _tasso_piece_label(Path(path).stem)
        raw_labels.setdefault(label, [])
        if path not in raw_labels[label]:
            raw_labels[label].append(path)

    return _dedupe_labels(raw_labels)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_seils_pieces():
    """{label: repo path} for SEILS (small: 32 real pieces, Italian
    secular songs ca. 1600 -- composer folder names are already real
    surnames, e.g. 'Alberti', 'Bardi'). Titles are NOT clean here --
    checked directly and these files carry no !!!OTL/!!!COM header
    metadata at all (unlike JRP/1520s/Tasso), and the compressed
    filenames (e.g. 'alberti_dalmio_mn') don't reliably split back into
    real words programmatically -- rather than guess-reconstruct a title
    that might be wrong, the raw filename fragment is shown as-is. Also
    filters out the '_annotation' duplicate of each piece (an OMR/
    analysis-annotated copy of the same music, confirmed by checking
    file pairs directly) so each piece appears once, not twice."""
    response = requests.get(
        'https://api.github.com/repos/SEILSdataset/SEILSdataset/git/trees/master',
        params={'recursive': '1'}, timeout=20,
    )
    response.raise_for_status()
    tree = response.json().get('tree', [])

    raw_labels = {}
    for item in tree:
        path = item['path']
        if not path.endswith('.krn') or 'SEILS_with_annotations' not in path:
            continue
        # Scoped to the filename, NOT the whole path: the parent folder
        # is itself named 'SEILS_with_annotations', which contains
        # '_annotation' as a substring -- checking the full path against
        # that excluded every single file, real pieces included (caught
        # directly: an all-path check returned zero results before this
        # fix). '_annotat' (not '_annotation') catches both duplicate
        # suffixes actually used here -- '_annotated.krn' AND
        # '_annotation.krn', two different endings for the same kind of
        # duplicate, confirmed against the real file list.
        filename = path.rsplit('/', 1)[-1]
        if '_annotat' in filename:
            continue
        parts = path.split('/')
        composer, stem = parts[2], Path(path).stem
        title = stem.split('_', 1)[1] if '_' in stem else stem  # drop composer-surname prefix
        label = f'{composer} — {title}'
        raw_labels.setdefault(label, [])
        if path not in raw_labels[label]:
            raw_labels[label].append(path)

    return _dedupe_labels(raw_labels)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_lassus_psalms_pieces():
    """{label: repo path} for Lassus's Geistliche Psalmen (50 psalm
    settings) -- a single-composer collection, so just a clean title per
    piece (filenames are already 'NN-title-words.krn', no code/catalog
    prefix to strip)."""
    response = requests.get(
        'https://api.github.com/repos/WolfgangDrescher/lassus-geistliche-psalmen/git/trees/master',
        params={'recursive': '1'}, timeout=20,
    )
    response.raise_for_status()
    tree = response.json().get('tree', [])

    raw_labels = {}
    for item in tree:
        path = item['path']
        if not path.endswith('.krn'):
            continue
        stem = Path(path).stem
        title = re.sub(r'^\d+-', '', stem).replace('-', ' ').capitalize()
        raw_labels.setdefault(title, [])
        if path not in raw_labels[title]:
            raw_labels[title].append(path)

    return _dedupe_labels(raw_labels)


def _safe_cadences(piece):
    """piece.cadences(voice_detail=True, include_final=True), guarded
    against a real crim_intervals bug -- confirmed directly by reading its
    cvfs() source and reproducing it against two actual pieces
    (monteverdi/madrigal.4.3, monteverdi/madrigal.4.18) before writing
    this, not assumed from the traceback alone: when a piece produces
    literally zero cadence-pattern-ngram hits anywhere in the whole
    piece, cvfs() does `df[['LowerVoice', 'UpperVoice']] = voices` with
    `voices` an empty list on a 0-row DataFrame, which pandas refuses
    ("Columns must be same length as key") -- not a network/data issue,
    a real gap in the library's own empty-result handling, so there's
    nothing to fix on our end beyond not letting it take the whole app
    down. Returns (cadences_df_or_None, error_message_or_None).
    """
    try:
        return piece.cadences(voice_detail=True, include_final=True), None
    except Exception as e:
        return None, (
            "Cadence detection failed for this piece -- a real bug in crim_intervals "
            f"itself (confirmed: {type(e).__name__}: {e}), not something wrong with "
            "your upload. It happens on pieces where crim_intervals finds zero "
            "cadence-like voice-pair patterns anywhere in the piece."
        )


def _append_timeline(stats, progress_values, label, color):
    """Appends one row per detected event to stats['timeline'] -- the
    data behind the strip-plot visualization rendered in show_result().
    Every one of CRIM's three analyses (cadences/presentationTypes/
    homorhythm) independently computes its own 'Progress' column the
    same way -- offset divided by the piece's last note offset, giving
    0-1 position through the piece (confirmed directly in all three
    methods' source before relying on it) -- so this is just collecting
    that column, tagged with which analysis it came from and that
    analysis's own notehead color (CADENCE_COLOR/PRESENTATION_COLOR/
    HOMORHYTHM_COLOR), so the plot's colors match the annotated score's
    colors exactly. Shared by cadences/ptypes/homorhythm in both
    run_pipeline() and _annotate_crim_piece() rather than duplicated six
    times across the two functions."""
    stats.setdefault('timeline', []).extend(
        {'Progress': p, 'Type': label, 'color': color} for p in progress_values
    )


def run_pipeline(score, source_label, include_cadences=True, include_ptypes=False, include_homorhythm=False):
    """Shared by both input modes below: given a parsed music21 Score,
    optionally runs each of CRIM's three structural analyses on it and
    writes whichever ones are requested onto the score. Returns
    (annotated_score, stats, error).

    include_cadences=True (the default, preserving this app's original
    behavior) runs CRIM cadence detection (voice_detail=True, for the
    PartMap this tool relies on -- see crim_export_cadences.py's
    docstring) and hands off to annotate_score() for the actual
    labeling/coloring. Set it False to skip cadences entirely -- e.g. to
    get only points-of-imitation/homorhythm, or, with all three flags
    False, a completely unmodified score for a plain download with zero
    CRIM computation at all.

    include_ptypes=True additionally runs presentationTypes() (points of
    imitation -- PEN/ID/FUGA) and marks those on the same score too, in a
    different color (see annotate_presentation_types); its stats are
    folded into the same dict under 'ptypes_labeled'/'ptypes_colored',
    only when this flag is set, so callers that never asked for it don't
    need to know the keys exist.

    include_homorhythm=True likewise runs homorhythm() (chordal, shared-
    text-declamation passages) and marks those too (see
    annotate_homorhythm), folding stats in under 'hr_labeled'/'hr_colored'.
    Unlike cadences()/presentationTypes(), homorhythm() returns a bare
    None (not an empty DataFrame) when nothing is found -- checked
    directly in its own source before relying on this -- so that's
    checked for explicitly rather than assumed away.

    error is None unless cadence detection itself failed (see
    _safe_cadences) -- ptypes/homorhythm are individually guarded too (a
    crash in either just skips that one optional feature -- see the
    'ptypes_failed'/'hr_failed' stats keys -- rather than losing an
    otherwise-successful annotation over it). Barring that error,
    annotated_score is never None -- if nothing was requested, or
    everything requested came up empty, the caller still gets a score
    back (unmodified in the former case) plus a stats dict that's empty
    or missing the relevant keys; show_result() reads that to decide
    what to tell the user, rather than this function refusing to return
    anything.
    """
    if not (include_cadences or include_ptypes or include_homorhythm):
        return score, {}, None

    # ci.ImportedPiece normally comes from ci.importScore(path_or_text),
    # which re-parses from scratch internally -- but it also accepts an
    # already-built music21 Score directly via its own constructor, which
    # avoids parsing the same piece twice (once for us, once for CRIM).
    piece = ci.main_objs.ImportedPiece(score, source_label)
    annotated_score, stats = score, {}

    if include_cadences:
        cadences, error = _safe_cadences(piece)
        if error:
            return None, None, error
        if not cadences.empty:
            annotated_score, stats = annotate_score(score, cadences)
            _append_timeline(stats, cadences['Progress'], 'Cadence', CADENCE_COLOR)
        else:
            stats = {'labeled': 0, 'missed_label': 0, 'colored': 0}

    if include_ptypes:
        try:
            ptypes = piece.presentationTypes()
        except Exception:
            ptypes = None
            stats['ptypes_failed'] = True
        if ptypes is not None and not ptypes.empty:
            annotated_score, ptype_stats = annotate_presentation_types(
                annotated_score, ptypes, piece._getPartNames()
            )
            stats['ptypes_labeled'] = ptype_stats['labeled']
            stats['ptypes_colored'] = ptype_stats['colored']
            _append_timeline(stats, ptypes['Progress'], 'Points of Imitation', PRESENTATION_COLOR)

    if include_homorhythm:
        try:
            hr = piece.homorhythm()
        except Exception:
            hr = None
            stats['hr_failed'] = True
        if hr is not None and not hr.empty:
            annotated_score, hr_stats = annotate_homorhythm(
                annotated_score, hr, piece._getPartNames()
            )
            stats['hr_labeled'] = hr_stats['labeled']
            stats['hr_colored'] = hr_stats['colored']
            _append_timeline(stats, hr['Progress'], 'Homorhythm', HOMORHYTHM_COLOR)

    return annotated_score, stats, None


def score_to_download_bytes(score):
    """music21's Score.write() wants a file path, not an in-memory buffer
    -- there's no direct 'give me bytes' API -- so we write to a real
    temp file and immediately read it back, then let the OS clean the
    temp file up. NamedTemporaryFile(delete=False) is needed on Windows
    specifically: an open NamedTemporaryFile can't be reopened by another
    process (music21's writer) while still held open in delete-on-close
    mode, unlike on Linux/Mac."""
    with NamedTemporaryFile(suffix='.xml', delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        score.write('musicxml', fp=str(tmp_path))
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)


# One sentence per analysis, written to read naturally whether one or all
# three are strung together -- see _build_methods_blurb(). Citation style
# (name + year/project, no full bibliography) deliberately matches what
# the rest of this app already uses elsewhere (README, the cadence-
# mechanism expander), not a separate convention invented just for this.
_METHODS_BLURB_MUSIC21 = "Scores were parsed with music21 (Cuthbert & Ariza, 2010)."
_METHODS_BLURB_CADENCES = (
    "Cadences were identified using CRIM Intervals' cadences() method (Morgan & "
    "Freedman, CRIM Project), which detects cadential voice functions (Cantizans, "
    "Tenorizans, Bassizans, etc.) via pairwise contrapuntal interval analysis "
    "rather than harmonic labeling."
)
_METHODS_BLURB_PTYPES = (
    "Points of imitation were identified using CRIM Intervals' presentationTypes() "
    "method, which finds melodic entries imitated across voices and classifies "
    "each instance as a Point of Entry, Imitative Duo, or Fuga based on the time "
    "intervals between successive entries."
)
_METHODS_BLURB_HOMORHYTHM = (
    "Homorhythmic passages were identified using CRIM Intervals' homorhythm() "
    "method, which finds passages where two or more voices share both rhythm and "
    "lyrics."
)


def _build_methods_blurb(include_cadences, include_ptypes, include_homorhythm):
    """A copy-pasteable methods-section paragraph describing exactly
    which analyses were actually requested for this download -- not
    which ones found anything, since "we ran cadence detection and it
    found none" is still a real, citable methodological fact. Returns
    None if nothing was requested at all (a plain download has no
    methods to describe). Deliberately takes the three include_* flags
    directly rather than trying to infer them from `stats` -- ptypes/
    homorhythm only add keys to `stats` when they find something, so
    "requested but empty" and "never requested" are indistinguishable
    from stats alone; the caller already has the real flags in scope.
    """
    sentences = [s for s, on in [
        (_METHODS_BLURB_CADENCES, include_cadences),
        (_METHODS_BLURB_PTYPES, include_ptypes),
        (_METHODS_BLURB_HOMORHYTHM, include_homorhythm),
    ] if on]
    if not sentences:
        return None
    return ' '.join([_METHODS_BLURB_MUSIC21] + sentences)


def show_result(annotated_score, stats, filename_stem, include_cadences=False, include_ptypes=False, include_homorhythm=False):
    """Reports whatever run_pipeline()/_annotate_crim_piece() actually
    did (cadences/ptypes/homorhythm are all optional now -- see
    run_pipeline's docstring) and offers the resulting file for
    download. `stats` can be empty (nothing was requested, or everything
    requested came up empty) -- annotated_score is still a real Score
    either way, just unmodified in that case, so a download is always
    offered; the filename/button text are the only things that change,
    honestly reflecting whether the file actually has anything written
    onto it. include_cadences/ptypes/homorhythm are only used to build
    the methods-section blurb below -- see _build_methods_blurb().
    """
    if 'labeled' in stats:
        st.success(
            f"{stats['labeled'] + stats['missed_label']} cadences found -- "
            f"{stats['labeled']} labeled, {stats['colored']} cadential notes colored."
        )
        if stats['missed_label']:
            st.info(
                f"{stats['missed_label']} cadence(s) couldn't be labeled (no matching "
                "measure found on the top staff) -- rare, usually means a metadata "
                "irregularity in that specific cadence's measure/beat."
            )
    if 'ptypes_labeled' in stats:
        st.success(
            f"{stats['ptypes_labeled']} point(s) of imitation found -- "
            f"{stats['ptypes_colored']} entry notes colored (blue)."
        )
    if 'hr_labeled' in stats:
        st.success(
            f"{stats['hr_labeled']} homorhythmic passage(s) found -- "
            f"{stats['hr_colored']} notes colored (green)."
        )
    if stats.get('ptypes_failed'):
        st.info(
            "Points-of-imitation detection hit an internal error on this piece and "
            "was skipped -- the rest of the annotation above is unaffected."
        )
    if stats.get('hr_failed'):
        st.info(
            "Homorhythm detection hit an internal error on this piece and was "
            "skipped -- the rest of the annotation above is unaffected."
        )

    # True only if something was actually written onto the score -- not
    # just requested. A feature that was checked but found nothing (or
    # nothing was checked at all) leaves stats without any of these keys,
    # and the file is a plain, unmodified score, not a broken annotation.
    annotated = any(k in stats for k in ('labeled', 'ptypes_labeled', 'hr_labeled'))
    if not annotated:
        st.info(
            "No structural annotations were added to this file -- either no "
            "analysis was selected, or none of the selected analyses found "
            "anything in this piece. The file below is the unmodified score."
        )

    # One row per detected event across whichever analyses ran -- see
    # _append_timeline(). Only present when `annotated` is True (nothing
    # is ever appended for an empty/unrequested analysis), so no separate
    # guard is needed beyond `if timeline`.
    timeline = stats.get('timeline')
    if timeline:
        st.caption("Where these occur across the piece (progress from start to end):")
        st.scatter_chart(pd.DataFrame(timeline), x='Progress', y='Type', color='color', height=200)

    methods_blurb = _build_methods_blurb(include_cadences, include_ptypes, include_homorhythm)
    if methods_blurb:
        with st.expander("📋 Methods-section description (for a paper)"):
            st.caption("Only mentions whichever analyses were actually requested above -- copy it as-is.")
            st.code(methods_blurb, language=None)

    xml_bytes = score_to_download_bytes(annotated_score)
    st.download_button(
        "Download annotated MusicXML" if annotated else "Download MusicXML",
        data=xml_bytes,
        file_name=f"{filename_stem}_annotated.xml" if annotated else f"{filename_stem}.xml",
        mime="application/vnd.recordare.musicxml+xml",
    )


with st.expander("What do the cadence labels mean?"):
    st.markdown(
        """
Each label is `<CadType> → <Tone>` -- the kind of cadence, and which
pitch class it resolves to (or the lowest sounding pitch, if the tone
itself was evaded -- see the CVF table below).

**Cadence types** CRIM recognizes (a realized type may also appear
prefixed `Evaded` or `Abandoned` when the expected voice-leading doesn't
fully complete): Authentic, Phrygian, Leaping Contratenor, Clausula Vera,
Phrygian Clausula Vera, Altizans Only, Phrygian Altizans, Double Leading
Tone, Quince, Reinterpreted. (Quince is rare, mostly in thicker textures
with a 5th voice; Reinterpreted is rarer still, where a pair of voices
that first sounds like a Cantizans-Bassizans pair gets reinterpreted
mid-cadence into an Altizans-Quintizans pair instead.)

**CVFs (Cadential Voice Functions)** -- the short code like `CTB` or
`Tcu` next to each cadence in the raw data names which voice performed
which contrapuntal role, in top-to-bottom staff order. **Uppercase =
fully realized, lowercase = evaded/abandoned.**

| Letter | Role | Motion |
|---|---|---|
| `C` | Cantizans | up a step (the "leading tone" voice) |
| `T` | Tenorizans | down a step |
| `B` | Bassizans | up a 4th / down a 5th |
| `A` | Altizans | like Cantizans, but resolves a 5th above the Tenorizans instead of an octave |
| `L` | Leaping Contratenor | up an octave at the arrival |
| `P` | Plagal Bassizans | up a 5th / down a 4th |
| `Q` | Quintizans | resolves a 4th below the Cantizans' goal tone (or an octave below the Altizans') |
| `S` | Sestizans | down a 3rd (thicker textures) |
| `c` | Cantizans, evaded | moves to an unexpected note at the arrival |
| `t` | Tenorizans, evaded | goes up a step instead of down |
| `b` | Bassizans, evaded | goes up a step instead of its expected leap |
| `u` | Bassizans, evaded | goes down a 3rd instead |
| `s` | Sestizans, evaded | resolves down a 2nd instead of a 3rd |
| `x` | Bassizans, abandoned | the voice drops out at the arrival |
| `y` | Cantizans, abandoned | the voice drops out at the arrival |
| `z` | Tenorizans, abandoned | the voice drops out at the arrival |

So e.g. `Tcu` (an example straight from Agnus_00's own output): the
Tenorizans resolves normally, but the Cantizans lands somewhere
unexpected and the Bassizans drops a 3rd instead of leaping -- a cadence
CRIM detected the shape of, but that doesn't fully "land," which is why
rows like this often have a blank CadType (no complete standard pattern
matched) rather than one of the named types above.

Source: [CRIM Intervals' own documentation](https://github.com/HCDigitalScholarship/intervals)
(`ImportedPiece.cadences`/`.cvfs` docstrings) -- reproduced here, not
reinterpreted, so it stays accurate if CRIM's own definitions change.
        """
    )

with st.expander("How does the cadence detector actually work?"):
    st.markdown(
        """
It doesn't look at the full chord at once -- it looks at **pairs of
voices**, and tracks two things between each pair over time: the
harmonic interval separating them (3rd, 5th, octave, ...) and each
voice's own melodic motion (step up, step down, leap, by how much). A
cadence, contrapuntally, is really a small number of well-known
two-voice interval progressions -- e.g. a major 6th expanding to an
octave (a classic Cantizans-Tenorizans pair), or a major 3rd contracting
to a unison. CRIM has a table of these named two-voice patterns
(`CVFLabels.csv`, inside the library itself) and scans **every pair of
voices** in the piece for a match, at every point in time.

**Why it needs at least 2 voices in the file, but works fine on a
2-voice passage inside a bigger piece:** with only 1 voice there's no
pair to compare at all -- there's no "other voice" to measure an
interval against, so nothing can ever match. But a passage where only 2
of a piece's, say, 4 voices happen to be sounding (the others resting)
works completely normally -- that's not a special case, it's the exact
situation the whole method is built around. (This is also the real
reason the two music21-bundled composers left out of the Composer
dropdown above don't work here: their files are a single monophonic
voice, full stop -- not "a 2-voice passage," genuinely no second voice
to pair with, ever.)
        """
    )

with st.expander("What do the points-of-imitation labels mean?"):
    st.markdown(
        """
This is a separate feature from cadences -- turn it on with the "Also
mark points of imitation" checkbox next to Annotate. It runs CRIM's
`presentationTypes()`, which finds **where a melodic idea (a "soggetto")
enters in one voice and is then imitated by others** -- the other
hallmark structural feature of this repertoire, alongside cadences.

**How it finds them:** it looks at each voice's melodic line and marks
"entries" -- a short run of notes (4 by default) that starts right after
a rest, a fermata, or a section break, since that's where a new
melodic idea is most likely to be freshly stated. It then checks every
other voice for a similar run of notes (allowing for transposition, and
optionally a little melodic "flex") appearing at a *later* offset. A
group of two or more such matching entries across different voices,
close enough together in time, becomes one "presentation type" instance.

**The three labels you'll see** -- CRIM classifies each instance by the
*pattern of time gaps* between successive entries (checked directly in
its source -- there's no fuller plain-language definition in CRIM's own
documentation beyond this, so this reflects what the code actually does,
not an assumption). The label on the score uses plain language, not
CRIM's own short codes (shown here in parentheses only for anyone
cross-referencing CRIM's own tools/output):
- **Point of Entry** (CRIM's code: `PEN`) -- every entry is spaced from
  the next by the *exact same* time interval -- a strict,
  regularly-staggered entry.
- **Imitative Duo** (`ID`) -- a smaller, odd-numbered, alternating group
  of entries -- typically two voices trading a short motive back and
  forth.
- **Imitative Entry** (`FUGA`) -- everything else -- the general case,
  most often several voices (3+) each taking up the same subject one
  after another, less strictly regular than a Point of Entry. CRIM's own
  code name uses the 16th-century sense of the Latin/Italian word
  "fuga" (voices "fleeing" one after another) -- deliberately not shown
  on the score itself, since to a modern eye it reads as a claim about
  the later, much stricter Baroque fugue, which this isn't.

**What actually appears on the score:** the note where each voice's
entry begins is colored **blue** (cadences are red, so both survive on
one file together), and the *first* entry of each instance gets a text
label naming its type (e.g. `Imitative Entry`), placed above the top staff at that
same beat -- same placement convention as cadence labels, so it always
reads cleanly above the system regardless of which voice enters first.
        """
    )

with st.expander("What do the homorhythm labels mean?"):
    st.markdown(
        """
A third, separate feature -- turn it on with the "Also mark homorhythmic
passages" checkbox next to Annotate. It runs CRIM's `homorhythm()`,
which finds **passages where two or more voices move together in the
same rhythm while singing the same words** -- a chordal, declamatory
texture, as opposed to the independent, staggered melodic lines that
cadences and points of imitation are both built around. This is the
other main way this repertoire varies its texture: strict counterpoint
punctuated by moments where the voices briefly line up and declaim text
together, often at a structurally important line of the text.

**How it finds them:** it looks at short runs of notes (4 by default) in
every voice at once, two ways in parallel -- matching **rhythm**
(each voice's own sequence of note durations) and matching **lyrics**
(each voice singing the same syllables at the same time) -- and keeps
only the passages where both line up across two or more voices that are
actually sounding (not resting). By default it requires *every* active
voice to match, not just some of them, for a passage to count.

**What actually appears on the score:** every note belonging to a
matching passage is colored **green** (cadences are red, points of
imitation are blue, so all three survive on one file together), and one
`Homorhythm` text label marks the start of each passage, placed above
the top staff, higher than the cadence and points-of-imitation labels so
none of the three ever overlap even when they coincide.
        """
    )

def _annotate_crim_piece(mei_url, include_cadences=True, include_ptypes=False, include_homorhythm=False):
    """Shared by the CRIM tab and Browse: import + metadata-fix, then
    whichever of CRIM's three analyses are requested, for one CRIM MEI
    piece. Returns (annotated_score, stats, error) -- error is None on
    success, or a message string if CRIM's own import failed, or if
    cadence detection itself failed (see _safe_cadences). include_
    cadences/ptypes/homorhythm all default/behave exactly as in
    run_pipeline's docstring -- see that for what each adds to `stats`,
    and why annotated_score is never None barring an actual error."""
    piece = ci.importScore(mei_url)
    if piece is None:
        return None, None, "CRIM couldn't import this piece (bad MEI file or network issue)."
    # ci.importScore extracts title/composer from the MEI header into
    # piece.metadata (its own plain dict) -- but NOT into piece.score.
    # metadata (the actual music21 Metadata object Score.write() reads
    # from), so left alone the exported file ends up with no real title/
    # composer and music21's writer falls back to generic placeholder
    # text ("Music21 Fragment"/"Music21" -- confirmed directly).
    piece.score.metadata.title = piece.metadata.get('title') or piece.score.metadata.title
    piece.score.metadata.composer = piece.metadata.get('composer') or piece.score.metadata.composer

    if not (include_cadences or include_ptypes or include_homorhythm):
        return piece.score, {}, None

    annotated_score, stats = piece.score, {}
    if include_cadences:
        cadences, error = _safe_cadences(piece)
        if error:
            return None, None, error
        if not cadences.empty:
            annotated_score, stats = annotate_score(piece.score, cadences)
            _append_timeline(stats, cadences['Progress'], 'Cadence', CADENCE_COLOR)
        else:
            stats = {'labeled': 0, 'missed_label': 0, 'colored': 0}
    if include_ptypes:
        try:
            ptypes = piece.presentationTypes()
        except Exception:
            ptypes = None
            stats['ptypes_failed'] = True
        if ptypes is not None and not ptypes.empty:
            annotated_score, ptype_stats = annotate_presentation_types(
                annotated_score, ptypes, piece._getPartNames()
            )
            stats['ptypes_labeled'] = ptype_stats['labeled']
            stats['ptypes_colored'] = ptype_stats['colored']
            _append_timeline(stats, ptypes['Progress'], 'Points of Imitation', PRESENTATION_COLOR)
    if include_homorhythm:
        try:
            hr = piece.homorhythm()
        except Exception:
            hr = None
            stats['hr_failed'] = True
        if hr is not None and not hr.empty:
            annotated_score, hr_stats = annotate_homorhythm(
                annotated_score, hr, piece._getPartNames()
            )
            stats['hr_labeled'] = hr_stats['labeled']
            stats['hr_colored'] = hr_stats['colored']
            _append_timeline(stats, hr['Progress'], 'Homorhythm', HOMORHYTHM_COLOR)
    return annotated_score, stats, None


def _annotate_kern_from_url(raw_url, source_label, include_cadences=True, include_ptypes=False, include_homorhythm=False):
    """Shared by every GitHub-hosted Humdrum kern tab (JRP, 1520s, Tasso,
    SEILS, Lassus Psalms): fetch the raw file, parse, run the pipeline.
    Humdrum **kern auto-detects fine from raw text content, same as
    music21's converter.parse does for MusicXML/MEI elsewhere in this
    app -- verified directly before relying on it."""
    kern_text = requests.get(raw_url, timeout=20).text
    score = m21.converter.parse(kern_text)
    return run_pipeline(
        score, source_label, include_cadences=include_cadences,
        include_ptypes=include_ptypes, include_homorhythm=include_homorhythm,
    )


# Base raw-content URLs for the five kern-backed collections, shared by
# _annotate_kern_from_url call sites and by Browse's preview/annotate paths.
KERN_COLLECTION_BASE_URLS = {
    'jrp': 'https://raw.githubusercontent.com/josquin-research-project/jrp-scores/main/',
    '1520s': 'https://raw.githubusercontent.com/benory/1520s-project-scores/main/',
    'tasso': 'https://raw.githubusercontent.com/TassoInMusicProject/tasso-scores/master/',
    'seils': 'https://raw.githubusercontent.com/SEILSdataset/SEILSdataset/master/',
    'lassus_psalms': 'https://raw.githubusercontent.com/WolfgangDrescher/lassus-geistliche-psalmen/master/',
}


@st.cache_data(ttl=3600, show_spinner=False)
def build_browse_index():
    """One flat list of (display_label, collection, native_ref) spanning
    all 7 collections (~4,300 pieces) -- built by tagging each
    collection's own already-built {label: id} dict with a '[Collection]'
    prefix, NOT by restructuring any of them into a new shared schema
    (every existing per-collection tab's code is untouched by this).
    native_ref is exactly whatever that collection's own fetcher already
    uses as a dict value: (corpus_key, piece_id) for music21, the full
    piece dict for CRIM, a repo path string for the five kern collections.

    Per-row `composer`/`genre` fields and Collection/Composer/Genre
    filter widgets were all tried here and removed: Collection duplicated
    what the dedicated per-collection tabs already do; composer naming
    isn't normalized across collections (e.g. CRIM's 'Josquin Des Prez'
    vs JRP's 'Josquin des Prez' would show up as separate filter values
    for the same composer), making a flat composer list more confusing
    than useful; and genre was only ever populated for CRIM, making a
    cross-collection genre filter mostly empty for every other source.
    Genre filtering now lives on the CRIM tab itself instead, where every
    piece actually has one (see tab_crim below) -- the right place for a
    facet that only one collection supports, rather than a cross-
    collection filter here.

    First call is slow-ish (calls every collection's own fetcher, several
    of which are themselves slow on a cold cache -- the metadata bundle
    alone takes ~7s to load the first time) but each of those is already
    @st.cache_data(ttl=3600) on its own, and this function is too, so
    that cost is paid once per hour, shared across every user hitting
    this server process, not once per visitor.
    """
    rows = []
    for composer_name, corpus_key in CORPUS_COMPOSERS.items():
        for label, piece_id in list_pieces_for_composer(corpus_key).items():
            rows.append((f'[{composer_name}] {label}', 'music21', (corpus_key, piece_id)))
    for p in fetch_crim_pieces():
        label = f"{p['composer']['name']} — {p['full_title']} [{p['genre']['name']}]"
        rows.append((f'[CRIM] {label}', 'crim', p))
    for key, fetch_fn, prefix in [
        ('jrp', fetch_jrp_pieces, 'JRP'), ('1520s', fetch_1520s_pieces, '1520s'),
        ('tasso', fetch_tasso_pieces, 'Tasso'), ('seils', fetch_seils_pieces, 'SEILS'),
        ('lassus_psalms', fetch_lassus_psalms_pieces, 'Lassus Psalms'),
    ]:
        for label, path in fetch_fn().items():
            rows.append((f'[{prefix}] {label}', key, path))
    return rows


def _local_corpus_file_path(corpus_key, piece_id):
    """The actual on-disk file for one already-known piece in a
    music21-bundled composer's corpus, matched by stem. Prefers a real
    score file over a same-stem non-score companion: checked directly
    and found monteverdi ships 49 real '.mxl' scores plus 48 '.rntxt'
    roman-numeral-analysis text files sharing stems with them (the same
    duplication already handled for labels in list_pieces_for_composer)
    -- .rntxt has no note/lyric content at all, so it's actively wrong
    to read for this, not just redundant."""
    candidates = [p for p in m21.corpus.getComposer(corpus_key) if p.stem == piece_id]
    real_scores = [p for p in candidates if p.suffix != '.rntxt']
    return real_scores[0] if real_scores else (candidates[0] if candidates else None)


def _local_file_stats(file_path):
    """(voices, has_text) for a local music21-corpus file, read directly
    from raw file content rather than either a full music21 parse OR
    music21's own metadata bundle -- the bundle was tried first and
    rejected: checked directly and found it only indexes 5 of
    Monteverdi's 49 real '.mxl' scores, silently falling back to the
    same-stem '.rntxt' (roman-numeral-analysis text, not a real
    multi-voice score) for the other 44 -- which reports 1 voice
    regardless of the piece's real voice count, confirmed on
    'madrigal.3.1' (bundle said 1, real parse said 5). Reading the
    actual score file directly sidesteps that gap entirely.

    Palestrina ships plain '.krn' (voices = '**kern' tokens on the
    spine-declaration line, has-text = whether a '**text' spine is also
    there -- same convention already used for the five GitHub kern
    collections). Monteverdi ships '.mxl' (a zip containing real
    MusicXML -- voices = '<score-part ' tag count, has-text = whether
    '<lyric' appears -- both checked directly against real files, not
    assumed from the format spec).

    Encoding caveat that cost a real, high-impact bug before this fix:
    checked all 49 of Monteverdi's real '.mxl' files directly and found
    37 of them (76%!) are internally UTF-16, not UTF-8 (Sibelius/Dolet
    export) -- blindly decoding as UTF-8 doesn't raise an error, it just
    silently garbles the text (errors='replace' masks it further), so
    '<score-part '/'<lyric' never match and everything quietly reports
    as unknown/no. Caught via a real example in testing ('A un Giro sol
    de' Belli Occhi Lucenti' showing 'Voices: unknown' where a real
    5-voice madrigal should show 5) -- not something the earlier
    2-sample spot check happened to hit, since both of those samples
    were coincidentally UTF-8. Fixed by detecting the UTF-16 BOM
    (b'\\xff\\xfe' or b'\\xfe\\xff') in the raw bytes before choosing a
    codec, rather than assuming UTF-8 uniformly."""
    if file_path is None:
        return None, None
    if file_path.suffix == '.krn':
        text = file_path.read_text(encoding='utf-8', errors='replace')
        spine_line = next((line for line in text.split('\n') if line.startswith('**')), '')
        return spine_line.count('**kern') or None, '**text' in spine_line
    if file_path.suffix == '.mxl':
        try:
            with zipfile.ZipFile(file_path) as z:
                inner_name = next(n for n in z.namelist() if n.endswith('.xml') and 'META-INF' not in n)
                raw = z.read(inner_name)
            if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
                xml_text = raw.decode('utf-16')
            else:
                xml_text = raw.decode('utf-8', errors='replace')
        except Exception:
            return None, None
        return xml_text.count('<score-part ') or None, '<lyric' in xml_text
    if file_path.suffix == '.xml':
        raw = file_path.read_bytes()
        if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
            text = raw.decode('utf-16')
        else:
            text = raw.decode('utf-8', errors='replace')
        return text.count('<score-part ') or None, '<lyric' in text
    return None, None  # an unanticipated format -- honestly unknown, not guessed


def preview_piece(collection, native_ref):
    """Returns (voices, has_text, note) for one piece WITHOUT running the
    full annotate pipeline -- voices/has_text are None where not cheaply
    determinable. Every branch was checked against real data before
    writing it, not assumed uniform across all 7 sources:
    - CRIM: voice count is already free in the piece-list JSON
      ('number_of_voices'); has-text needs one MEI fetch, checked via a
      raw '<verse'/'<syl' tag search rather than a full MEI parse.
    - music21 corpus (Palestrina/Monteverdi): both voices and has-text
      are read directly from the local file (see _local_file_stats) --
      NOT from the metadata bundle's 'numberOfParts', which was tried
      first and found unreliable (see that function's docstring: only
      5 of Monteverdi's 49 real scores are actually indexed in the
      bundle). No network involved either way, since these ship inside
      the music21 package itself.
    - The five kern collections: one raw-file fetch (files are tiny, a
      few KB to tens of KB, confirmed earlier in this session), then
      voice count from counting '**kern' tokens on the spine-declaration
      line, and has-text from whether a '**text' spine is present on
      that same line -- both checked directly against real files from
      all five collections before relying on this.
    """
    if collection == 'crim':
        p = native_ref
        voices = p.get('number_of_voices')
        try:
            mei_text = requests.get(p['mei_links'][0], timeout=20).text
            has_text = ('<verse' in mei_text) or ('<syl' in mei_text)
        except Exception:
            has_text = None
        return voices, has_text, None

    if collection == 'music21':
        corpus_key, piece_id = native_ref
        file_path = _local_corpus_file_path(corpus_key, piece_id)
        voices, has_text = _local_file_stats(file_path)
        return voices, has_text, None

    try:
        text = requests.get(KERN_COLLECTION_BASE_URLS[collection] + native_ref, timeout=20).text
    except Exception:
        return None, None, "Couldn't fetch this file to preview it."
    spine_line = next((line for line in text.split('\n') if line.startswith('**')), '')
    voices = spine_line.count('**kern') or None
    has_text = '**text' in spine_line
    return voices, has_text, None


def _browse_piece_filename_stem(collection, native_ref):
    """The stem used for the downloaded annotated file's name -- differs
    by collection because native_ref's shape differs (see
    build_browse_index's docstring)."""
    if collection == 'music21':
        return native_ref[1]
    if collection == 'crim':
        return native_ref['piece_id']
    return Path(native_ref).stem


def _browse_row_to_csv_dict(label, collection, native_ref):
    """One CSV row for Browse's "download search results as CSV" export
    -- source_url/music21_corpus_path are meant to be directly usable in
    someone's own Python script (plain requests.get()/converter.parse()
    for source_url; m21.corpus.parse() for music21_corpus_path) without
    touching this app at all -- the actual point of a manifest export.
    Reuses the exact same collection-dispatch shape as
    annotate_by_collection(), just building a URL/path instead of
    fetching+annotating.

    composer is parsed back out of `label` -- safe since it's this app's
    own generated string (see build_browse_index), not third-party data.
    Two different conventions to undo depending on collection: music21
    labels are '[ComposerName] Title' (composer *is* the bracket tag,
    see list_pieces_for_composer), while every other collection's labels
    are '[CollectionTag] Composer — Title' (composer follows the tag,
    separated by an em dash -- see _catalog_piece_label/
    _tasso_piece_label/fetch_seils_pieces/fetch_crim_pieces). Composer-
    naming inconsistency across collections (documented at length in
    build_browse_index's git history) isn't a problem here the way it
    was for a filter dropdown -- a CSV column showing both spellings
    as data is just honest, not confusing.
    """
    if collection == 'music21':
        composer = label.split('] ', 1)[0].lstrip('[')
    else:
        inner = label.split('] ', 1)[1] if '] ' in label else label
        composer = inner.partition(' — ')[0]

    row = {'collection': collection, 'composer': composer, 'label': label,
           'source_url': '', 'music21_corpus_path': ''}
    if collection == 'music21':
        corpus_key, piece_id = native_ref
        row['music21_corpus_path'] = f'{corpus_key}/{piece_id}'
    elif collection == 'crim':
        row['source_url'] = native_ref['mei_links'][0]
    else:
        row['source_url'] = KERN_COLLECTION_BASE_URLS[collection] + native_ref
    return row


def _matches_to_csv_bytes(matches):
    """The full match list (NOT capped to the 50 shown in the picker --
    see tab_browse below) as UTF-8 CSV bytes, ready for
    st.download_button. Plain csv.DictWriter + io.StringIO rather than
    pandas -- this app has no other reason to depend on pandas directly
    (crim_intervals pulls it in transitively, but nothing here has
    imported it so far), so no new dependency for one CSV export."""
    buf = io.StringIO()
    fieldnames = ['collection', 'composer', 'label', 'source_url', 'music21_corpus_path']
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for label, collection, native_ref in matches:
        writer.writerow(_browse_row_to_csv_dict(label, collection, native_ref))
    return buf.getvalue().encode('utf-8')


def annotate_by_collection(collection, native_ref, include_cadences=True, include_ptypes=False, include_homorhythm=False):
    """Dispatches to whichever collection's own annotate path applies --
    reuses the exact same functions each dedicated tab already calls, so
    Browse's Download button behaves identically to picking the same
    piece from its own tab, not a separate reimplementation. Returns
    (annotated_score, stats, error_message)."""
    if collection == 'music21':
        corpus_key, piece_id = native_ref
        score = m21.corpus.parse(f"{corpus_key}/{piece_id}")
        return run_pipeline(
            score, piece_id, include_cadences=include_cadences,
            include_ptypes=include_ptypes, include_homorhythm=include_homorhythm,
        )
    if collection == 'crim':
        return _annotate_crim_piece(
            native_ref['mei_links'][0], include_cadences=include_cadences,
            include_ptypes=include_ptypes, include_homorhythm=include_homorhythm,
        )
    raw_url = KERN_COLLECTION_BASE_URLS[collection] + native_ref
    return _annotate_kern_from_url(
        raw_url, Path(native_ref).stem, include_cadences=include_cadences,
        include_ptypes=include_ptypes, include_homorhythm=include_homorhythm,
    )


# Hard cap on Browse's "download all matches as ZIP" -- benchmarked directly
# against 8 real JRP pieces (fetch + music21 parse + MusicXML conversion, no
# CRIM at all): 2.6s-7.8s each, 5.25s average. 30 pieces keeps the whole
# operation under ~3 minutes; past that, sitting through a single Streamlit
# progress bar is a bad way to wait, and the CSV export (instant, any size)
# is the better fit for a bigger result set anyway -- not a silent truncation,
# the UI refuses outright and says why (see tab_browse below).
BULK_ZIP_MAX_MATCHES = 30


def _bulk_zip_bytes(matches, progress_callback=None):
    """Fetches + parses + converts every match to MusicXML -- no CRIM
    analysis at all (the same "all three flags off" path already used
    for single-piece unannotated downloads, see run_pipeline's
    docstring) -- and zips them into one in-memory archive. Returns
    (zip_bytes, failed) where `failed` is a list of (label, reason) for
    any piece that couldn't be fetched/parsed -- skipped rather than
    aborting the whole batch, but reported explicitly rather than
    silently missing from the zip. progress_callback(index, total,
    label), if given, is called right before each piece starts."""
    buf = io.BytesIO()
    failed = []
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, (label, collection, native_ref) in enumerate(matches):
            if progress_callback:
                progress_callback(i, len(matches), label)
            try:
                score, _stats, error = annotate_by_collection(
                    collection, native_ref, include_cadences=False,
                    include_ptypes=False, include_homorhythm=False,
                )
                if error:
                    raise RuntimeError(error)
                xml_bytes = score_to_download_bytes(score)
            except Exception as e:
                failed.append((label, str(e)))
                continue
            stem = _browse_piece_filename_stem(collection, native_ref)
            zf.writestr(f'{stem}.xml', xml_bytes)
    return buf.getvalue(), failed


def render_preview_and_annotate(collection, native_ref, piece_label, filename_stem, key_prefix=None):
    """Two-column Preview/Download buttons -- shared by every dedicated
    collection tab AND Browse (same layout, same underlying calls), so a
    given piece behaves identically no matter which tab you reach it
    from. Built on preview_piece()/annotate_by_collection(), not a
    separate per-tab reimplementation.

    key_prefix defaults to `collection`, but Browse passes 'browse'
    explicitly: since every tab's widgets are mounted simultaneously
    (Streamlit doesn't scope keys by which tab is visually active --
    confirmed directly earlier in this project), reusing e.g. 'crim' as
    the key from BOTH the CRIM tab and Browse (when a CRIM piece is
    selected there) would collide -- two different widgets can't share
    one key in the same script run."""
    key_prefix = key_prefix or collection
    # Cadences default to checked (this app's original, still-primary
    # feature); unchecking all three gives back a completely unmodified
    # score -- see run_pipeline's docstring -- so "download without
    # annotation" is just "uncheck everything" rather than a separate flow.
    include_cadences = st.checkbox(
        "Annotate cadences", value=True, key=f"cadences_{key_prefix}",
    )
    include_ptypes = st.checkbox(
        "Also mark points of imitation", key=f"ptypes_{key_prefix}",
    )
    include_homorhythm = st.checkbox(
        "Also mark homorhythmic passages", key=f"hr_{key_prefix}",
    )
    col1, col2 = st.columns(2)
    if col1.button("Preview", key=f"preview_{key_prefix}"):
        with st.spinner("Checking..."):
            voices, has_text, note = preview_piece(collection, native_ref)
        st.write(f"**Voices:** {voices if voices is not None else 'unknown'}")
        has_text_display = 'yes' if has_text else ('no' if has_text is False else 'unknown')
        st.write(f"**Has encoded text/lyrics:** {has_text_display}")
        if note:
            st.caption(note)
    if col2.button("Download", key=f"annotate_{key_prefix}"):
        with st.spinner(f"Downloading and parsing {piece_label}..."):
            try:
                annotated_score, stats, error = annotate_by_collection(
                    collection, native_ref, include_cadences=include_cadences,
                    include_ptypes=include_ptypes, include_homorhythm=include_homorhythm,
                )
            except Exception as e:
                # A safety net, not the primary fix -- _safe_cadences already
                # catches the one specific crim_intervals bug found so far
                # (see its docstring). This is a backstop for anything else
                # (a different library edge case, a network hiccup mid-parse)
                # that would otherwise crash the whole app instead of just
                # failing this one piece -- confirmed necessary directly: a
                # real user hit exactly this kind of uncaught crash before
                # this fix existed.
                annotated_score, stats, error = None, None, f"Unexpected error analyzing this piece: {e}"
        if error:
            st.error(error)
        else:
            show_result(
                annotated_score, stats, filename_stem, include_cadences=include_cadences,
                include_ptypes=include_ptypes, include_homorhythm=include_homorhythm,
            )



# "Upload your own file" is 2nd, right after Browse -- NOT last, where it
# used to sit: as the 8th of 8 tabs it fell past the visible tab-bar width
# on a typical window, needing a scroll/arrow-click to even see it exists
# (checked directly by resizing the running app). Browse and Upload are
# also the two source-agnostic entry points (search everything vs. bring
# your own file), so pairing them first is a reasonable grouping on its
# own merits, not just a width hack. The upload icon mirrors Browse's own
# leading emoji so both "generic" tabs read as a visually distinct pair
# at a glance, before reading any label text.
tab_browse, tab_upload, tab_corpus, tab_crim, tab_jrp, tab_1520s, tab_tasso, tab_smaller = st.tabs([
    "🔍 Browse all", "📤 Upload your own file", "music21 corpus", "CRIM Project corpus",
    "Josquin Research Project", "1520s Project", "Tasso in Music Project", "More collections",
])

with tab_browse:
    st.caption(
        "Search all ~4,300 pieces across every collection in this app at once, "
        "preview a piece's voice count and whether it has encoded text/lyrics "
        "before committing to the full analysis, then annotate it directly -- "
        "or download every match at once, as a CSV manifest (any size) or a "
        "ZIP of raw MusicXML scores (up to 30 at a time)."
    )
    query = st.text_input("Search by composer or title", key="browse_query")

    if query:
        with st.spinner("Searching (first search after a quiet spell indexes all "
                         "collections, can take up to ~15s -- instant after that)..."):
            index = build_browse_index()
        matches = [row for row in index if query.lower() in row[0].lower()]

        if not matches:
            st.info("No matches.")
        else:
            shown = matches[:50]
            st.caption(f"{len(matches)} match(es)" + (" -- showing first 50" if len(matches) > 50 else ""))
            st.download_button(
                f"📄 Download all {len(matches)} match(es) as CSV",
                data=_matches_to_csv_bytes(matches),
                file_name="browse_results.csv",
                mime="text/csv",
                key="browse_csv_download",
                help="A manifest of every match (not just the 50 shown below) -- collection, "
                     "composer, and a source URL or music21 corpus path for each, ready to "
                     "load with pandas and fetch/parse in your own script.",
            )

            if len(matches) > BULK_ZIP_MAX_MATCHES:
                st.caption(
                    f"📦 Bulk ZIP download works for up to {BULK_ZIP_MAX_MATCHES} matches at once "
                    f"(this search has {len(matches)}) -- narrow the search to enable it, or use "
                    "the CSV above for the full list."
                )
            elif st.button(f"📦 Build a ZIP of all {len(matches)} score(s) (MusicXML)", key="browse_zip_build"):
                progress_bar = st.progress(0.0)
                status = st.empty()

                def _update_zip_progress(i, total, label):
                    progress_bar.progress(i / total)
                    status.caption(f"Fetching {i + 1}/{total}: {label}")

                with st.spinner("Building ZIP -- fetching, parsing, and converting each "
                                 "piece to MusicXML, no analysis run on any of them..."):
                    zip_bytes, failed = _bulk_zip_bytes(matches, progress_callback=_update_zip_progress)
                progress_bar.progress(1.0)
                status.empty()

                if failed:
                    detail = "; ".join(f"{label} ({reason})" for label, reason in failed[:5])
                    st.warning(
                        f"{len(failed)} of {len(matches)} piece(s) couldn't be included and were "
                        f"skipped: {detail}" + (", ..." if len(failed) > 5 else "")
                    )
                st.download_button(
                    f"Download ZIP ({len(matches) - len(failed)} score(s))",
                    data=zip_bytes,
                    file_name="browse_results.zip",
                    mime="application/zip",
                    key="browse_zip_download",
                )

            browse_label = st.selectbox("Pick one", [m[0] for m in shown], key="browse_pick")
            _, collection, native_ref = next(m for m in shown if m[0] == browse_label)
            stem = _browse_piece_filename_stem(collection, native_ref)
            render_preview_and_annotate(collection, native_ref, browse_label, stem, key_prefix='browse')

with tab_upload:
    st.caption("Accepted formats: MusicXML (.xml/.musicxml) or MEI (.mei).")
    uploaded = st.file_uploader("Score file", type=['xml', 'musicxml', 'mei'])
    include_cadences_upload = st.checkbox("Annotate cadences", value=True, key="cadences_upload")
    include_ptypes_upload = st.checkbox("Also mark points of imitation", key="ptypes_upload")
    include_homorhythm_upload = st.checkbox("Also mark homorhythmic passages", key="hr_upload")
    if uploaded is not None and st.button("Download", key="annotate_upload"):
        with st.spinner("Parsing upload..."):
            # Decoding to text and handing the STRING (not a file path) to
            # music21/CRIM is the same pattern crim_intervals' own code
            # documents for "user-supplied piece in streamlit" (see this
            # file's module docstring) -- it skips the on-disk extension
            # check entirely and lets converter.parse() sniff the format
            # from the content itself.
            text = uploaded.getvalue().decode('utf-8')
            score = m21.converter.parse(text)
            try:
                annotated_score, stats, error = run_pipeline(
                    score, uploaded.name, include_cadences=include_cadences_upload,
                    include_ptypes=include_ptypes_upload, include_homorhythm=include_homorhythm_upload,
                )
            except Exception as e:
                annotated_score, stats, error = None, None, f"Unexpected error analyzing this piece: {e}"
        if error:
            st.error(error)
        else:
            show_result(
                annotated_score, stats, Path(uploaded.name).stem, include_cadences=include_cadences_upload,
                include_ptypes=include_ptypes_upload, include_homorhythm=include_homorhythm_upload,
            )

with tab_corpus:
    composer_name = st.selectbox("Composer", sorted(CORPUS_COMPOSERS.keys()))
    corpus_key = CORPUS_COMPOSERS[composer_name]
    piece_options = list_pieces_for_composer(corpus_key)
    piece_label = st.selectbox("Piece", sorted(piece_options.keys()))
    piece_id = piece_options[piece_label]
    render_preview_and_annotate('music21', (corpus_key, piece_id), piece_label, piece_id)

with tab_crim:
    st.caption(
        "359 pieces from the CRIM Project (Lassus's parody masses, plus the "
        "polyphonic models -- motets, chansons, madrigals -- they're based on), "
        "fetched live from crimproject.org."
    )
    crim_pieces = fetch_crim_pieces()
    # Genre filter lives here, not on Browse -- CRIM is the only collection
    # that actually carries genre metadata, so it's a real, always-populated
    # facet in this tab specifically, rather than a mostly-empty one bolted
    # onto a cross-collection search (see build_browse_index's docstring).
    genre_filter = st.multiselect(
        "Filter by genre", sorted({p['genre']['name'] for p in crim_pieces}), key="crim_genre_filter",
    )
    if genre_filter:
        crim_pieces = [p for p in crim_pieces if p['genre']['name'] in genre_filter]

    # label -> full piece dict, so selecting a label gets us straight back to
    # its mei_links entry without a second lookup pass
    crim_options = {
        f"{p['composer']['name']} — {p['full_title']} [{p['genre']['name']}]": p
        for p in crim_pieces
    }
    if not crim_options:
        st.info("No pieces match that genre filter.")
    else:
        crim_label = st.selectbox("Piece", sorted(crim_options.keys()))
        selected = crim_options[crim_label]
        render_preview_and_annotate('crim', selected, crim_label, selected['piece_id'])

with tab_jrp:
    st.caption(
        "1,340+ pieces from the Josquin Research Project (Josquin, Ockeghem, "
        "Obrecht, la Rue, Gaspar van Weerbeke, and 15+ more early Franco-Flemish "
        "composers), fetched live from their public GitHub repository."
    )
    jrp_pieces = fetch_jrp_pieces()
    jrp_label = st.selectbox("Piece", sorted(jrp_pieces.keys()), key="jrp_piece")
    path = jrp_pieces[jrp_label]
    render_preview_and_annotate('jrp', path, jrp_label, Path(path).stem)

with tab_1520s:
    st.caption(
        "662 pieces from The 1520s Project (ca. 1510-1540 music, mostly France, "
        "Germany, Italy, and the Low Countries -- 38 composers plus anonymous "
        "works), fetched live from their public GitHub repository."
    )
    p1520_pieces = fetch_1520s_pieces()
    p1520_label = st.selectbox("Piece", sorted(p1520_pieces.keys()), key="p1520_piece")
    path = p1520_pieces[p1520_label]
    render_preview_and_annotate('1520s', path, p1520_label, Path(path).stem)

with tab_tasso:
    st.caption(
        "503 madrigal settings of Torquato Tasso's poetry (mostly 1570s-1640s, "
        "many composers) from the Tasso in Music Project, fetched live from "
        "their public GitHub repository."
    )
    tasso_pieces = fetch_tasso_pieces()
    tasso_label = st.selectbox("Piece", sorted(tasso_pieces.keys()), key="tasso_piece")
    path = tasso_pieces[tasso_label]
    render_preview_and_annotate('tasso', path, tasso_label, Path(path).stem)

with tab_smaller:
    st.caption("Two smaller collections, not big enough on their own to earn a full tab.")
    SMALLER_COLLECTIONS = {
        "SEILS (30 Italian secular songs, ca. 1600)": ('seils', fetch_seils_pieces),
        "Lassus -- Geistliche Psalmen (50 psalm settings)": ('lassus_psalms', fetch_lassus_psalms_pieces),
    }
    collection_name = st.selectbox("Collection", sorted(SMALLER_COLLECTIONS.keys()))
    collection_key, fetch_fn = SMALLER_COLLECTIONS[collection_name]
    small_pieces = fetch_fn()
    small_label = st.selectbox("Piece", sorted(small_pieces.keys()), key="small_piece")
    path = small_pieces[small_label]
    render_preview_and_annotate(collection_key, path, small_label, Path(path).stem)
