"""
CADENCE ANNOTATOR -- a small Streamlit web app wrapping the same pipeline
as annotate_piece_pipeline.py, but with a point-and-click UI so colleagues
without a Python/conda setup can use it from a browser tab (once it's
deployed somewhere -- see DEPLOY.md in this folder for that step).

Everything below runs in a single process, in the `crim` conda env (same
env that runs crim_export_cadences.py / annotate_cadences.py on the
command line -- see those files' docstrings for why one env is enough
here). Streamlit re-runs this whole script top-to-bottom on every user
interaction (button click, dropdown change, etc.) -- that's normal for
Streamlit, not a bug; it's why there's no explicit event-loop code below.

"""
import html
import re
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

import music21 as m21
import requests
import streamlit as st

# so `from annotate_cadences import ...` finds the file regardless of the
# directory `streamlit run` was launched from
sys.path.insert(0, str(Path(__file__).parent))
from annotate_cadences import annotate_score
from crim_export_cadences import export_cadences_with_partmap  # noqa: F401 (kept for reference)
import crim_intervals as ci

st.set_page_config(page_title="Cadence Annotator", layout="centered")
st.title("Cadence Annotator")
st.caption(
    "Runs CRIM Intervals cadence detection and writes the result back onto "
    "the score itself -- a text label + colored notes at every cadence -- "
    "so you can open the output directly in MuseScore or Finale."
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


def run_pipeline(score, source_label):
    """Shared by both input modes below: given a parsed music21 Score,
    run CRIM cadence detection (voice_detail=True, for the PartMap this
    tool relies on -- see crim_export_cadences.py's docstring) directly
    on it, then hand off to annotate_score() for the actual labeling/
    coloring. Returns the annotated Score plus a stats dict.
    """
    # ci.ImportedPiece normally comes from ci.importScore(path_or_text),
    # which re-parses from scratch internally -- but it also accepts an
    # already-built music21 Score directly via its own constructor, which
    # avoids parsing the same piece twice (once for us, once for CRIM).
    piece = ci.main_objs.ImportedPiece(score, source_label)
    cadences = piece.cadences(voice_detail=True, include_final=True)

    if cadences.empty:
        return None, None

    annotated_score, stats = annotate_score(score, cadences)
    return annotated_score, stats


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


def show_result(annotated_score, stats, out_filename):
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
    xml_bytes = score_to_download_bytes(annotated_score)
    st.download_button(
        "Download annotated MusicXML",
        data=xml_bytes,
        file_name=out_filename,
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

def _annotate_kern_from_url(raw_url, source_label):
    """Shared by every GitHub-hosted Humdrum kern tab (JRP, 1520s, Tasso,
    SEILS, Lassus Psalms): fetch the raw file, parse, run the pipeline.
    Humdrum **kern auto-detects fine from raw text content, same as
    music21's converter.parse does for MusicXML/MEI elsewhere in this
    app -- verified directly before relying on it."""
    kern_text = requests.get(raw_url, timeout=20).text
    score = m21.converter.parse(kern_text)
    return run_pipeline(score, source_label)


tab_corpus, tab_crim, tab_jrp, tab_1520s, tab_tasso, tab_smaller, tab_upload = st.tabs([
    "music21 corpus", "CRIM Project corpus", "Josquin Research Project",
    "1520s Project", "Tasso in Music Project", "More collections", "Upload your own file",
])

with tab_corpus:
    composer_name = st.selectbox("Composer", sorted(CORPUS_COMPOSERS.keys()))
    corpus_key = CORPUS_COMPOSERS[composer_name]
    piece_options = list_pieces_for_composer(corpus_key)
    piece_label = st.selectbox("Piece", sorted(piece_options.keys()))
    piece_id = piece_options[piece_label]
    if st.button("Annotate", key="annotate_corpus"):
        with st.spinner(f"Parsing {piece_label} and detecting cadences..."):
            score = m21.corpus.parse(f"{corpus_key}/{piece_id}")
            annotated_score, stats = run_pipeline(score, piece_id)
        if annotated_score is None:
            st.warning("No cadences were detected in this piece (too short, or not "
                       "polyphonic enough for CRIM's cadence model to find anything).")
        else:
            show_result(annotated_score, stats, f"{piece_id}_annotated.xml")

with tab_crim:
    st.caption(
        "359 pieces from the CRIM Project (Lassus's parody masses, plus the "
        "polyphonic models -- motets, chansons, madrigals -- they're based on), "
        "fetched live from crimproject.org."
    )
    crim_pieces = fetch_crim_pieces()
    # label -> full piece dict, so selecting a label gets us straight back to
    # its mei_links entry without a second lookup pass
    crim_options = {
        f"{p['composer']['name']} — {p['full_title']} [{p['genre']['name']}]": p
        for p in crim_pieces
    }
    crim_label = st.selectbox("Piece", sorted(crim_options.keys()))
    if st.button("Annotate", key="annotate_crim"):
        selected = crim_options[crim_label]
        mei_url = selected['mei_links'][0]
        annotated_score, stats, import_failed = None, None, False
        with st.spinner(f"Downloading and parsing {selected['piece_id']} from CRIM..."):
            # ci.importScore accepts a URL directly (per its own docstring) and
            # returns an ImportedPiece already wrapping the parsed music21
            # Score -- no need to separately fetch+parse ourselves here, unlike
            # the other two tabs where we build the Score first.
            piece = ci.importScore(mei_url)
            if piece is None:
                import_failed = True
            else:
                # ci.importScore extracts title/composer from the MEI header
                # into piece.metadata (its own plain dict) -- but NOT into
                # piece.score.metadata (the actual music21 Metadata object
                # Score.write() reads from), so left alone the exported file
                # ends up with no real title/composer and music21's writer
                # falls back to generic placeholder text ("Music21 Fragment"/
                # "Music21" -- confirmed directly: piece.score.metadata.title
                # was None even though piece.metadata['title'] had the real
                # value). Copy them across before anything gets written out.
                piece.score.metadata.title = piece.metadata.get('title') or piece.score.metadata.title
                piece.score.metadata.composer = piece.metadata.get('composer') or piece.score.metadata.composer

                cadences = piece.cadences(voice_detail=True, include_final=True)
                if not cadences.empty:
                    annotated_score, stats = annotate_score(piece.score, cadences)

        if import_failed:
            st.error("CRIM couldn't import this piece (bad MEI file or network issue).")
        elif annotated_score is None:
            st.warning("No cadences were detected in this piece.")
        else:
            show_result(annotated_score, stats, f"{selected['piece_id']}_annotated.xml")

with tab_jrp:
    st.caption(
        "1,340+ pieces from the Josquin Research Project (Josquin, Ockeghem, "
        "Obrecht, la Rue, Gaspar van Weerbeke, and 15+ more early Franco-Flemish "
        "composers), fetched live from their public GitHub repository."
    )
    jrp_pieces = fetch_jrp_pieces()
    jrp_label = st.selectbox("Piece", sorted(jrp_pieces.keys()), key="jrp_piece")
    if st.button("Annotate", key="annotate_jrp"):
        path = jrp_pieces[jrp_label]
        raw_url = f"https://raw.githubusercontent.com/josquin-research-project/jrp-scores/main/{path}"
        with st.spinner(f"Downloading and parsing {jrp_label}..."):
            annotated_score, stats = _annotate_kern_from_url(raw_url, Path(path).stem)
        if annotated_score is None:
            st.warning("No cadences were detected in this piece.")
        else:
            show_result(annotated_score, stats, f"{Path(path).stem}_annotated.xml")

with tab_1520s:
    st.caption(
        "662 pieces from The 1520s Project (ca. 1510-1540 music, mostly France, "
        "Germany, Italy, and the Low Countries -- 38 composers plus anonymous "
        "works), fetched live from their public GitHub repository."
    )
    p1520_pieces = fetch_1520s_pieces()
    p1520_label = st.selectbox("Piece", sorted(p1520_pieces.keys()), key="p1520_piece")
    if st.button("Annotate", key="annotate_1520s"):
        path = p1520_pieces[p1520_label]
        raw_url = f"https://raw.githubusercontent.com/benory/1520s-project-scores/main/{path}"
        with st.spinner(f"Downloading and parsing {p1520_label}..."):
            annotated_score, stats = _annotate_kern_from_url(raw_url, Path(path).stem)
        if annotated_score is None:
            st.warning("No cadences were detected in this piece.")
        else:
            show_result(annotated_score, stats, f"{Path(path).stem}_annotated.xml")

with tab_tasso:
    st.caption(
        "503 madrigal settings of Torquato Tasso's poetry (mostly 1570s-1640s, "
        "many composers) from the Tasso in Music Project, fetched live from "
        "their public GitHub repository."
    )
    tasso_pieces = fetch_tasso_pieces()
    tasso_label = st.selectbox("Piece", sorted(tasso_pieces.keys()), key="tasso_piece")
    if st.button("Annotate", key="annotate_tasso"):
        path = tasso_pieces[tasso_label]
        raw_url = f"https://raw.githubusercontent.com/TassoInMusicProject/tasso-scores/master/{path}"
        with st.spinner(f"Downloading and parsing {tasso_label}..."):
            annotated_score, stats = _annotate_kern_from_url(raw_url, Path(path).stem)
        if annotated_score is None:
            st.warning("No cadences were detected in this piece.")
        else:
            show_result(annotated_score, stats, f"{Path(path).stem}_annotated.xml")

with tab_smaller:
    st.caption("Two smaller collections, not big enough on their own to earn a full tab.")
    SMALLER_COLLECTIONS = {
        "SEILS (30 Italian secular songs, ca. 1600)": (
            fetch_seils_pieces, "https://raw.githubusercontent.com/SEILSdataset/SEILSdataset/master/"
        ),
        "Lassus -- Geistliche Psalmen (50 psalm settings)": (
            fetch_lassus_psalms_pieces,
            "https://raw.githubusercontent.com/WolfgangDrescher/lassus-geistliche-psalmen/master/",
        ),
    }
    collection_name = st.selectbox("Collection", sorted(SMALLER_COLLECTIONS.keys()))
    fetch_fn, base_url = SMALLER_COLLECTIONS[collection_name]
    small_pieces = fetch_fn()
    small_label = st.selectbox("Piece", sorted(small_pieces.keys()), key="small_piece")
    if st.button("Annotate", key="annotate_small"):
        path = small_pieces[small_label]
        raw_url = base_url + path
        with st.spinner(f"Downloading and parsing {small_label}..."):
            annotated_score, stats = _annotate_kern_from_url(raw_url, Path(path).stem)
        if annotated_score is None:
            st.warning("No cadences were detected in this piece.")
        else:
            show_result(annotated_score, stats, f"{Path(path).stem}_annotated.xml")

with tab_upload:
    st.caption("Accepted formats: MusicXML (.xml/.musicxml) or MEI (.mei).")
    uploaded = st.file_uploader("Score file", type=['xml', 'musicxml', 'mei'])
    if uploaded is not None and st.button("Annotate", key="annotate_upload"):
        with st.spinner("Parsing upload and detecting cadences..."):
            # Decoding to text and handing the STRING (not a file path) to
            # music21/CRIM is the same pattern crim_intervals' own code
            # documents for "user-supplied piece in streamlit" (see this
            # file's module docstring) -- it skips the on-disk extension
            # check entirely and lets converter.parse() sniff the format
            # from the content itself.
            text = uploaded.getvalue().decode('utf-8')
            score = m21.converter.parse(text)
            annotated_score, stats = run_pipeline(score, uploaded.name)
        if annotated_score is None:
            st.warning("No cadences were detected in this piece.")
        else:
            stem = Path(uploaded.name).stem
            show_result(annotated_score, stats, f"{stem}_annotated.xml")
