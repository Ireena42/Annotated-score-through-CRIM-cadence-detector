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

Three ways to pick a piece:
  1. a dropdown of the ids already bundled in music21's Palestrina corpus
     (same ids used throughout findings.md/todo.md, e.g. 'Agnus_15')
  2. a dropdown of the CRIM Project's own online corpus (359 pieces, 48
     composers, live-fetched from crimproject.org -- see below)
  3. uploading your own MusicXML/MEI file directly

WHY THE CRIM CORPUS TAB, NOT MORE music21 COMPOSERS: checked music21's
other bundled composers before adding anything (directory listing +
piece counts, not assumed) -- besides Palestrina, the only other
Renaissance/sacred-adjacent entries are either a single piece each
(lusitano, luca, ciconia) or not actually sacred polyphony (josquin's
bundled set is secular ABC-notation chansons; monteverdi is secular
madrigals). Padding the UI with those wouldn't have delivered "more
sacred polyphony" in practice. CRIM's own corpus does: it's Lassus's
parody masses (~300 movements) plus the polyphonic models they're based
on -- motets/chansons/madrigals by Willaert, Rore, Morales, Guerrero,
Palestrina himself, and 40+ other 16th-century composers -- fetched live
from https://crimproject.org/data/pieces/ (the same public JSON endpoint
CRIM's own reference Streamlit app in this repo, intervals_streamlit2.py,
uses -- checked its source before building this, per this project's
"verify, don't assume" habit).

WHY NOT HUMDRUM (.krn) UPLOAD: crim_intervals' own reference Streamlit
app only offers 'mei'/'xml' in its file_uploader -- Humdrum-from-a-raw-
string isn't demonstrated anywhere in CRIM's own code, so it isn't
promised to work reliably here either. Corpus pieces (which mostly START
as Humdrum .krn) still work fine via the dropdown, since music21's own
corpus loader handles that format from its own bundled files -- only a
raw uploaded .krn file's content is untested territory.
"""
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
            label = md.title or stem
        raw_labels.setdefault(label, [])
        if stem not in raw_labels[label]:
            raw_labels[label].append(stem)

    result = {}
    for label, stems in raw_labels.items():
        if len(stems) == 1:
            result[label] = stems[0]
        else:
            for stem in stems:
                result[f'{label} [{stem}]'] = stem
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

**What it genuinely can't see:** a moment where a voice pair's interval
is literally `Rest` (checked directly in CRIM's own code -- a rest
isn't blank/missing data, it's an explicit `'Rest'` value in the
interval table) instead of a real interval. None of CRIM's coded
patterns expect `Rest` mid-progression, so if most or all voices stop
sounding right where a cadence would otherwise land, nothing matches --
even though, musically, that silence right before a resolution is often
a very deliberate expressive gesture. Fixing that specific class of miss
isn't a small edit to CRIM's cadence-type table -- the whole method is
built on sounding voice *pairs*, so it would need a genuinely separate
detection strategy layered on top (e.g. explicitly watching for a
simultaneous multi-voice rest following built-up approach motion), not
a patch to the existing one.
        """
    )

tab_corpus, tab_crim, tab_upload = st.tabs(
    ["music21 corpus", "CRIM Project corpus", "Upload your own file"]
)

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
