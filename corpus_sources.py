"""
Piece-enumeration layer for the Renaissance Polyphony Research Toolkit --
extracted out of app.py (not reimplemented) so a piece other than app.py
itself -- specifically scripts/precompute_finalis.py -- can list exactly
the same ~4,300 pieces across all 7 collections without a second, drifting
copy of this logic. Every function here is pure Python (network + parsing
only); none of them call any Streamlit UI function directly, only the
_cache_data/_cache_resource decorators below wrap them from outside.

_cache_data/_cache_resource are Streamlit's own st.cache_data/st.cache_
resource when streamlit is importable (true inside the actual deployed
app -- identical caching behavior to before this file existed), and a
plain no-op passthrough when it isn't (true inside a standalone script
like precompute_finalis.py, which has no reason to install streamlit
just for this). Caching a one-shot batch script's own fetches wouldn't
survive between separate process runs anyway (st.cache_data's default
persist=None is memory-only), so the no-op fallback costs that context
nothing real.
"""
import html
import re
import time
from pathlib import Path

import music21 as m21
import requests


def _get_with_retry(url, max_attempts=3, backoff_seconds=5, **kwargs):
    """requests.get() with retry-on-transient-failure -- added after a
    real production incident, not speculatively: a single unretried
    504 Gateway Timeout from GitHub's own API (a transient blip on
    GitHub's own infrastructure, nothing wrong with the request itself)
    killed an entire multi-hour precompute run before it did any real
    work at all, since every fetch_*() function below runs once, right
    at the very start of build_browse_index() -- before any of the
    actual per-piece computation that run existed to do (confirmed
    directly from the real failed run's own traceback, 2026-09-02).

    Retries on a connection-level exception, or on a 429/5xx status
    code specifically -- a 4xx (bad URL, not found) wouldn't be fixed
    by retrying and is left to raise immediately via the caller's own
    raise_for_status(), same as before. Linear backoff (5s, 10s, ...),
    not exponential: these are occasional infrastructure blips on
    GitHub's/CRIM's side, not a service under sustained load that needs
    aggressive backoff. Returns the (possibly still-failing) response
    on the final attempt rather than raising itself, so the caller's
    own raise_for_status() stays the single place that turns a bad
    response into an exception, unchanged from before this existed.
    """
    last_exception = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(url, **kwargs)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < max_attempts:
                time.sleep(backoff_seconds * attempt)
                continue
            return response
        except requests.exceptions.RequestException as e:
            last_exception = e
            if attempt < max_attempts:
                time.sleep(backoff_seconds * attempt)
    raise last_exception

try:
    import streamlit as st
    _cache_data = st.cache_data(ttl=3600, show_spinner=False)
    # No ttl, unlike _cache_data above: music21's bundled corpus is a
    # static package resource, not live network data, so there's nothing
    # to periodically re-fetch -- matches list_pieces_for_composer's own
    # original @st.cache_data(show_spinner=False) exactly (no ttl=3600).
    _cache_data_no_ttl = st.cache_data(show_spinner=False)
    _cache_resource = st.cache_resource(show_spinner=False)
except ImportError:
    def _cache_data(fn):
        return fn

    def _cache_data_no_ttl(fn):
        return fn

    def _cache_resource(fn):
        return fn


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


@_cache_resource
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


@_cache_data_no_ttl
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


@_cache_data
def fetch_crim_pieces():
    """The CRIM Project's full piece list, live from their public API --
    verified directly (curl) before writing this: 359 pieces, 48 composers,
    each entry carrying piece_id/title/composer/genre/mei_links. Cached for
    1 hour (ttl=3600, same value intervals_streamlit2.py uses for the same
    call) so a page full of users doesn't refetch this on every rerun --
    Streamlit reruns the whole script on every interaction, so without
    caching this would hit crimproject.org on every single button click,
    dropdown change, etc.
    """
    response = _get_with_retry('https://crimproject.org/data/pieces/', timeout=15)
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
    response = _get_with_retry(
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


@_cache_data
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


@_cache_data
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


@_cache_data
def fetch_tasso_pieces():
    """{label: repo path} for the Tasso in Music Project (madrigal
    settings of Torquato Tasso's poetry, mostly 1570s-1640s, many
    composers) -- 503 real pieces across 8 genre-code folders (Tam, Tbv,
    Tco, Tec, Tri, Trm, Trt, Tsg -- different Tasso poem collections, not
    composers; composer comes from the filename itself, see
    _tasso_piece_label). No composer-code allowlist needed here (unlike
    JRP/1520s) since every genre folder is real content, verified
    directly (zero redirect-stub-sized files in the whole repo)."""
    response = _get_with_retry(
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


@_cache_data
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
    response = _get_with_retry(
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


@_cache_data
def fetch_lassus_psalms_pieces():
    """{label: repo path} for Lassus's Geistliche Psalmen (50 psalm
    settings) -- a single-composer collection, so just a clean title per
    piece (filenames are already 'NN-title-words.krn', no code/catalog
    prefix to strip)."""
    response = _get_with_retry(
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


# Base raw-content URLs for the five kern-backed collections, shared by
# app.py's _annotate_kern_from_url/_import_piece_by_collection call sites
# and by precompute_finalis.py.
KERN_COLLECTION_BASE_URLS = {
    'jrp': 'https://raw.githubusercontent.com/josquin-research-project/jrp-scores/main/',
    '1520s': 'https://raw.githubusercontent.com/benory/1520s-project-scores/main/',
    'tasso': 'https://raw.githubusercontent.com/TassoInMusicProject/tasso-scores/master/',
    'seils': 'https://raw.githubusercontent.com/SEILSdataset/SEILSdataset/master/',
    'lassus_psalms': 'https://raw.githubusercontent.com/WolfgangDrescher/lassus-geistliche-psalmen/master/',
}


@_cache_data
def build_browse_index():
    """One flat list of (display_label, collection, native_ref) spanning
    all 7 collections (~4,300 pieces) -- built by tagging each
    collection's own already-built {label: id} dict with a '[Collection]'
    prefix, NOT by restructuring any of them into a new shared schema
    (every existing per-collection tab's code is untouched by this).
    native_ref is exactly whatever that collection's own fetcher already
    uses as a dict value: (corpus_key, piece_id) for music21, the full
    piece dict for CRIM, a repo path string for the five kern collections.

    First call is slow-ish (calls every collection's own fetcher, several
    of which are themselves slow on a cold cache -- the metadata bundle
    alone takes ~7s to load the first time) but each of those is already
    cached (ttl=3600) on its own inside the app, and this function is
    too, so that cost is paid once per hour, shared across every user
    hitting this server process, not once per visitor. Inside
    precompute_finalis.py, none of that caching applies (see this
    module's own docstring) -- every call there is a real, uncached
    fetch, which is fine for a one-shot batch script.
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
