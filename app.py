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


@st.cache_data(show_spinner=False)
def list_palestrina_ids():
    """Every piece id in music21's bundled Palestrina corpus, e.g.
    'Agnus_00'. Cached (st.cache_data) so this filesystem scan -- cheap,
    but pointless to repeat -- only runs once per app process, not once
    per button click/rerun."""
    paths = m21.corpus.getComposer('palestrina')
    return sorted(p.stem for p in paths)


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

tab_corpus, tab_crim, tab_upload = st.tabs(
    ["Built-in Palestrina corpus", "CRIM Project corpus", "Upload your own file"]
)

with tab_corpus:
    piece_id = st.selectbox("Piece", list_palestrina_ids())
    if st.button("Annotate", key="annotate_corpus"):
        with st.spinner(f"Parsing {piece_id} and detecting cadences..."):
            score = m21.corpus.parse(f"palestrina/{piece_id}")
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
