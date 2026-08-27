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

Two ways to pick a piece:
  1. a dropdown of the ids already bundled in music21's Palestrina corpus
     (same ids used throughout findings.md/todo.md, e.g. 'Agnus_15')
  2. uploading your own MusicXML/MEI file directly

WHY NOT HUMDRUM (.krn) UPLOAD: crim_intervals' own reference Streamlit
app (this project's intervals_streamlit2.py, inspected before writing this
file) only offers 'mei'/'xml' in its file_uploader -- Humdrum-from-a-raw-
string isn't demonstrated anywhere in CRIM's own code, so it isn't
promised to work reliably here either. Corpus pieces (which mostly START
as Humdrum .krn) still work fine via the dropdown, since music21's own
corpus loader handles that format from its own bundled files -- only a
raw uploaded .krn file's content is untested territory.
"""
import sys
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

import music21 as m21
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


tab_corpus, tab_upload = st.tabs(["Built-in Palestrina corpus", "Upload your own file"])

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
