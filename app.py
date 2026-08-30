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
import random
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree as ET

import music21 as m21
import pandas as pd
import requests
import streamlit as st
from wordcloud import WordCloud

# so `from annotate_cadences import ...` finds the file regardless of the
# directory `streamlit run` was launched from
sys.path.insert(0, str(Path(__file__).parent))
from annotate_cadences import (
    annotate_score, annotate_presentation_types, annotate_homorhythm,
    CADENCE_COLOR, PRESENTATION_COLOR, HOMORHYTHM_COLOR,
)
from crim_export_cadences import export_cadences_with_partmap  # noqa: F401 (kept for reference)
import crim_intervals as ci
from corpus_sources import (
    CORPUS_COMPOSERS, list_pieces_for_composer, fetch_crim_pieces, fetch_jrp_pieces,
    fetch_1520s_pieces, fetch_tasso_pieces, fetch_seils_pieces, fetch_lassus_psalms_pieces,
    KERN_COLLECTION_BASE_URLS, build_browse_index,
)  # piece-enumeration layer -- see corpus_sources.py's own module docstring for why
  # this moved out of app.py (precompute_finalis.py needs the identical logic too).

st.set_page_config(
    page_title="Renaissance Polyphony Research Toolkit",
    page_icon="favicon_semibreve.png",  # a void (outline) semibreve -- see this
    # file's own real notehead shapes below for the manuscript-derived motif.
    layout="centered",
)
# Visual identity, Option 3 from the identity-review mockup ("Bolder --
# warmer, more confident"): Fraunces for headings, IBM Plex Sans for
# body text. Colors themselves live in .streamlit/config.toml's [theme]
# section (Streamlit's own supported theming mechanism, robust across
# Streamlit versions) -- only the custom Google Fonts need injecting
# here, since config.toml's font option doesn't support arbitrary named
# fonts. Scoped to h1/h2/h3 (Streamlit's own tags for title/header/
# subheader) and body/caption text via their stable data-testid
# attributes, not a blanket wildcard selector -- Streamlit's icon fonts
# (expander arrows, etc.) rely on their own specific font-family and
# would break under a wildcard override.
st.markdown(
    """
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400..600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
    h1, h2, h3 { font-family: 'Fraunces', Georgia, serif !important; font-weight: 600; }
    body, [data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"],
    [data-testid="stWidgetLabel"], [data-testid="stMetricValue"] {
        font-family: 'IBM Plex Sans', Arial, sans-serif;
    }
    /* Density stat tiles (show_result's Cadence/Points of Imitation/
       Homorhythm percentages) -- st.metric's own default size reads as
       too prominent for what's meant to be a quick, secondary figure
       next to the strip plot, not a headline number. Only user of
       st.metric in this app (checked directly), so scoping this
       narrower isn't needed. */
    [data-testid="stMetricValue"] { font-size: 1.5rem; }
    /* Custos hover -- a small manuscript guide-mark that appears next
       to a button's label on hover, approved from the widget-ideas
       preview. Applied broadly to every button (checked directly:
       "stBaseButton-secondary" is the real, stable data-testid this
       app's own buttons render with), not narrowed to just Analyze/
       Download specifically -- Streamlit doesn't expose a per-button
       CSS hook based on its own label text, so narrowing further isn't
       reliably possible without risking it silently matching nothing.
       A pure-CSS ::after pseudo-element (not a JS-inserted sibling
       node), so it isn't affected by Streamlit's own React re-renders.
       Same custos path as the favicon/divider above, inlined as an SVG
       data URI since a CSS background-image can't reference this
       file's own <symbol> definitions. Stroke color is the parchment
       tone (not the dark ink used elsewhere for this same shape):
       every button that gets this hover is now styled type="primary"
       (orange fill, light text), so a light custos is what actually
       reads against that background -- the dark version would have
       had the same low-contrast problem the ink color was originally
       fine to avoid on the plain white/secondary buttons this replaced. */
    [data-testid^="stBaseButton"]:hover::after {
        content: "";
        display: inline-block;
        width: 15px; height: 11px;
        margin-left: 7px;
        vertical-align: middle;
        background: no-repeat center / contain url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 24'%3E%3Cpath d='M4 20 L14 4 L20 14 L28 4' fill='none' stroke='%23fbf3e7' stroke-width='4' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
    }
    /* The file uploader's own internal "Browse files" button -- has no
       Python-level type="primary" option (st.file_uploader doesn't
       expose one), so this is the only way to match it to every other
       action button now that they're all styled type="primary".
       Scoped to inside stFileUploaderDropzone specifically (checked
       directly against this app's real DOM) so it doesn't
       accidentally widen back out to buttons this rule isn't meant
       for. */
    [data-testid="stFileUploaderDropzone"] button {
        background-color: #a8451f;
        color: #fbf3e7;
        border-color: #a8451f;
    }
    /* The rule above only set the button's resting color -- Streamlit's own
       secondary-button hover/active rules (a near-transparent tan tint,
       meant for the plain white button this replaced) have higher CSS
       specificity and were still winning on hover/click, which is what made
       the button visibly flash instead of darkening like the type="primary"
       buttons elsewhere (confirmed by reading this button's real CSSOM rules
       on the deployed app, not guessed). Fixed by pinning :hover/:active to
       the exact colors Streamlit computes for its own primary buttons (read
       the same way), so this button now darkens identically instead of
       reverting to its native secondary look. */
    [data-testid="stFileUploaderDropzone"] button:hover,
    [data-testid="stFileUploaderDropzone"] button:focus-visible {
        background-color: #672b13;
        border-color: #672b13;
        color: #fbf3e7;
    }
    [data-testid="stFileUploaderDropzone"] button:active {
        background-color: #a8451f;
        border-color: #672b13;
        color: #fbf3e7;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("Renaissance Polyphony Research Toolkit")
st.caption(
    "This app works with symbolic notation of Renaissance polyphony -- "
    "MusicXML, Humdrum kern, MEI, not audio or MIDI -- scattered across "
    "several separate archives online. It gathers ~4,300 such pieces from "
    "7 sources into one searchable, analysis-ready place. Run CRIM's "
    "structural analyses (cadences, points of imitation, homorhythmic "
    "passages), see where they fall across the piece, and take the results "
    "further -- an annotated score for MuseScore/Finale, a PDF to read or "
    "print, a raw file for your own code, or a dataset across a whole search."
)


def _composer_from_collection_label(label):
    """Pulls the composer back out of a label built in this collection's
    own 'Composer — Rest' convention -- JRP/1520s/Tasso/SEILS (via
    _catalog_piece_label/_tasso_piece_label/fetch_seils_pieces) all use
    it. Safe to reuse HERE, within one collection's own tab -- unlike
    Browse's cross-collection composer filter (tried and reverted, see
    build_browse_index's docstring): that problem was specifically about
    mixing DIFFERENT collections' own naming conventions in one dropdown
    (CRIM's "Josquin Des Prez" vs JRP's "Josquin des Prez" showing up as
    separate values for the same person). A single collection's own
    labels are internally consistent by construction -- there's nothing
    to reconcile within just JRP, or just 1520s, on their own.

    A trailing '(YYYY)' is stripped -- Tasso's own composer convention
    bakes the specific print/publication year into this field (see
    _tasso_piece_label), which is a real fact about that one piece, not
    part of the composer's identity: left in, it fragmented what should
    be one filter entry into many near-duplicates (confirmed directly:
    "Bellasio (1578)"/"Bellasio (1590)"/"Bellasio (1591)" are the same
    person, and stripping the year collapsed Tasso's composer count from
    229 down to 149 real composers). Harmless no-op for every other
    collection, since none of them ever produce that suffix."""
    composer, sep, _ = label.partition(' — ')
    if not sep:
        return 'Unknown'
    return re.sub(r'\s*\(\d{4}\)$', '', composer)


def _composer_filter_widget(pieces_by_label, key):
    """Renders a "Composer" selectbox (one composer at a time, with an
    "All composers" default -- same interaction as the music21 tab's own
    Composer picker, not a multiselect: picking several composers at
    once mixed different people's pieces into one alphabetized list,
    which wasn't actually useful and broke consistency with the one
    Composer picker this app already had) scoped to one collection's own
    {label: native_ref} dict, and returns it filtered accordingly --
    unchanged if "All composers" stays selected, or if there's only one
    composer to begin with (a filter offering just one real choice is
    never worth showing -- e.g. Lassus's Geistliche Psalmen, a genuinely
    single-composer collection). Composer is parsed straight from each
    label, see _composer_from_collection_label -- these are collections
    that don't have genre as structured data (see the CRIM tab's own
    genre filter for the one collection that does), but they all do have
    a clean, internally-consistent composer per piece, which is a
    different kind of metadata than genre and happens to be available
    more broadly."""
    composers = sorted({_composer_from_collection_label(label) for label in pieces_by_label})
    if len(composers) <= 1:
        return pieces_by_label
    selected = st.selectbox("Composer", ["All composers"] + composers, key=key)
    if selected == "All composers":
        return pieces_by_label
    return {
        label: ref for label, ref in pieces_by_label.items()
        if _composer_from_collection_label(label) == selected
    }


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


def _append_timeline(stats, measure_values, label, color):
    """Appends one row per detected event to stats['timeline'] -- the
    data behind the strip-plot visualization rendered in show_result().
    Uses each event's actual measure number (e.g. 47), not CRIM's 0-1
    'Progress' fraction -- a measure number reads directly off the
    score, a fraction of total piece length doesn't. Cadences expose
    'Measure' as a plain column and homorhythm as an index level
    (confirmed directly in both methods' source); presentationTypes()
    exposes neither -- there, the calling site parses the first voice's
    entry measure out of its 'Measures_Beats' field instead (the same
    value annotate_presentation_types() itself tries first when placing
    that instance's own label), tagged with which analysis it came from
    and that analysis's own notehead color (CADENCE_COLOR/
    PRESENTATION_COLOR/HOMORHYTHM_COLOR), so the plot's colors match the
    annotated score's colors exactly. Shared by cadences/ptypes/
    homorhythm in both run_pipeline() and _annotate_crim_piece() rather
    than duplicated six times across the two functions."""
    stats.setdefault('timeline', []).extend(
        {'Measure': m, 'Type': label, 'color': color} for m in measure_values
    )


def _total_measures(score):
    """Total measure count for a piece, read from its top staff's own
    Measure objects -- the same "top staff" convention already used
    throughout annotate_cadences.py (measure_indices[0], the part
    labels actually get placed on) rather than a separate rule invented
    just for this."""
    return len(score.parts[0].getElementsByClass('Measure'))


def _add_density(stats, score):
    """Folds a density figure into stats['density'] for each analysis
    type present in stats['timeline'] -- the fraction of the piece's
    measures that contain at least one detected event of that type
    (e.g. 0.12 -- cadences fall in 12% of this piece's measures), not a
    raw event count, so a short piece and a long piece are directly
    comparable. Uses the SET of unique measure numbers a type's events
    touch, not len(events), since more than one event can land in the
    same measure (this matters most for homorhythm: crim_intervals'
    raw, non-consolidated sliding-window output -- see
    annotate_homorhythm's own docstring -- naturally produces several
    rows per real passage, several measures apart or overlapping; the
    unique-measures count is what actually answers "how much of the
    piece is homorhythmic," not "how many overlapping windows fired").
    A no-op if stats has no timeline (nothing was requested, or nothing
    was found) or the piece has zero measures (guards a division by
    zero on a degenerate/empty score rather than crashing)."""
    timeline = stats.get('timeline')
    total = _total_measures(score)
    if not timeline or not total:
        return
    by_type = {}
    for row in timeline:
        by_type.setdefault(row['Type'], set()).add(row['Measure'])
    stats['density'] = {label: len(measures) / total for label, measures in by_type.items()}


# Which edition/source a Renaissance encoding derives from matters a lot --
# see this project's own conversation history (Casimiri vs Jeppesen as
# editors of Palestrina's Opere Complete: different volumes were edited by
# different scholars, so no single blanket claim is honest -- surfacing
# whatever THIS piece's own file actually says is the only reliable
# option). Codes below checked directly against two real collections
# before choosing them, not guessed from the Humdrum reference-record
# spec alone: music21's bundled Palestrina files carry YOR/YOO (the
# original PRINT edition this encoding derives from, and its publisher --
# e.g. "Le Opere Complete, v. 18, p. 126" / "Rome, Italy: Fratelli
# Scalera", confirmed against the actual Agnus_00.krn file on GitHub),
# while JRP's carry SCA (the modern CRITICAL edition name itself, e.g.
# "New Josquin Edition 3.1") -- genuinely different fields for genuinely
# different kinds of source information, and neither collection has both.
#
# humdrum:RNB was tried here and removed: despite the generic Humdrum
# label, this corpus's own files actually use it for something entirely
# different -- a "Cadence finals: D" (or, on a real piece checked
# directly, "Cadence finals: G,C,A") summary, not a note about the
# SOURCE at all. Showing it as an "Editorial note" here was actively
# misleading, not just off-topic -- this exact field's meaning is
# already flagged elsewhere in this project as unverified (possibly
# describing the underlying cantus firmus/model chant, not the actual
# polyphonic setting), so it doesn't belong in a "where does this
# encoding come from" panel even under a better label.
_HUMDRUM_EDITION_FIELDS = [
    ('humdrum:SCA', 'Critical edition'),
    ('humdrum:SCT', 'Edition reference'),
    ('humdrum:YOR', 'Original print edition'),
    ('humdrum:YOO', 'Original publisher'),
    ('humdrum:PWK', 'Print/manuscript source'),
]


def _humdrum_edition_info(score):
    """Whatever edition/source fields this piece's own file actually
    encodes, read generically from music21's parsed Humdrum reference
    records (every '!!!XXX' record becomes a 'humdrum:XXX' key on
    Metadata.all() -- the same access pattern already used elsewhere in
    this project) rather than hard-coded per collection, since
    different sources populate different subsets of
    _HUMDRUM_EDITION_FIELDS. Covers every Humdrum-kern-derived
    collection this app has (music21 corpus, JRP, 1520s, Tasso, SEILS,
    Lassus Psalms) -- not CRIM, whose MEI files use a completely
    different, richer scheme (see _crim_edition_info). Returns a list
    of (label, value) pairs for only the fields actually present, in a
    fixed order -- empty if this score has no metadata at all, or none
    of these specific fields (never raises)."""
    try:
        meta = dict(score.metadata.all())
    except Exception:
        return []
    return [(label, str(meta[key])) for key, label in _HUMDRUM_EDITION_FIELDS if meta.get(key)]


_MEI_NS = {'mei': 'http://www.music-encoding.org/ns/mei'}


def _crim_edition_info(mei_url):
    """CRIM's own MEI files carry genuinely richer source documentation
    than a plain music21 Metadata parse surfaces (checked directly
    against two real CRIM pieces' actual MEI files before writing this):
    the real editors' names (<respStmt><persName role="editor">) and the
    original print source this transcription is based on -- title,
    publisher, date, physical repository, all under <manifestation>.
    None of this reliably lands in music21's own Metadata object after
    MEI import (MEI support there is comparatively thin, unlike
    Humdrum's), so this re-fetches the same MEI file's raw text
    (already fetched once by ci.importScore internally, but not exposed
    as text there) and parses just these specific elements directly --
    a real, small extra network request per CRIM piece, not free, but
    the only way to get at this. Returns a list of (label, value)
    pairs, empty (never raises) if the fetch fails or none of these
    elements are present."""
    try:
        response = requests.get(mei_url, timeout=15)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception:
        return []

    info = []
    editors = [
        el.text.strip() for el in root.findall('.//mei:respStmt/mei:persName[@role="editor"]', _MEI_NS)
        if el.text and el.text.strip()
    ]
    if editors:
        info.append(('Editors (this encoding)', ', '.join(editors)))

    manifestation = root.find('.//mei:manifestation', _MEI_NS)
    if manifestation is not None:
        title_el = manifestation.find('.//mei:titleStmt/mei:title', _MEI_NS)
        if title_el is not None and title_el.text and title_el.text.strip():
            info.append(('Original print source', title_el.text.strip()))

        pub_name_el = manifestation.find('.//mei:pubStmt/mei:publisher/mei:persName', _MEI_NS)
        date_el = manifestation.find('.//mei:pubStmt/mei:date', _MEI_NS)
        pub_bits = [
            el.text.strip() for el in (pub_name_el, date_el)
            if el is not None and el.text and el.text.strip()
        ]
        if pub_bits:
            info.append(('Original publisher/date', ', '.join(pub_bits)))

        corp_el = manifestation.find('.//mei:physLoc/mei:repository/mei:corpName', _MEI_NS)
        geog_el = manifestation.find('.//mei:physLoc/mei:repository/mei:geogName', _MEI_NS)
        loc_bits = [
            el.text.strip() for el in (corp_el, geog_el)
            if el is not None and el.text and el.text.strip()
        ]
        if loc_bits:
            info.append(('Source location', ', '.join(loc_bits)))

    return info


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
        stats = {}
        edition = _humdrum_edition_info(score)
        if edition:
            stats['edition'] = edition
        return score, stats, None

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
            _append_timeline(stats, cadences['Measure'], 'Cadence', CADENCE_COLOR)
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
            first_entry_measures = ptypes['Measures_Beats'].apply(
                lambda mb: int(float(mb[0].split('/')[0]))
            )
            _append_timeline(stats, first_entry_measures, 'Points of Imitation', PRESENTATION_COLOR)

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
            _append_timeline(stats, hr.index.get_level_values('Measure'), 'Homorhythm', HOMORHYTHM_COLOR)

    _add_density(stats, annotated_score)
    edition = _humdrum_edition_info(annotated_score)
    if edition:
        stats['edition'] = edition
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


def score_to_pdf_bytes(score):
    """PDF export via Verovio -- replaces an earlier attempt that went
    through music21's own LilyPond backend (score.write('lily.pdf')).
    That path had two real, confirmed problems, not just a style
    preference: every cadential note rendered in the SAME red regardless
    of which analysis actually colored it, and the cadence-type text
    labels (TextExpression) were silently dropped entirely -- music21's
    LilyPond translator re-derives its own from-scratch .ly markup from
    the Score object, and has real gaps in that translation. It also
    could crash outright on a zero-duration Note/Rest/Chord already
    present in some piece's own encoding (a separate, real bug in that
    translation layer, unrelated to this app's own annotation).

    Verovio sidesteps all of that by reading the SAME already-correct
    MusicXML the "Download MusicXML" button offers (via
    score_to_download_bytes) -- the exact <notehead color="..."> and
    <words> content MuseScore/Finale/Dorico already display correctly --
    instead of re-deriving its own notation from the music21 object
    model. Confirmed directly on a real annotated piece: the exact color
    used for cadence notes (#CC3333) and the literal label text
    ("Authentic -> G") both come through correctly in Verovio's output,
    nothing else tinted. It's also a strictly simpler dependency: pure
    pip packages (verovio/svglib/reportlab), no system binary and no
    packages.txt entry, unlike LilyPond -- and crim_intervals already
    depends on verovio itself for its own verovioCadences()/
    verovioPrintExample() Jupyter helpers, so this isn't a new library
    to the project, just a new use of one already installed.

    Verovio only renders to SVG, one page at a time, not directly to
    PDF -- so each page is converted to a reportlab Drawing (svglib) and
    drawn onto its own page of one PDF via reportlab's own vector
    renderer (renderPDF, not the raster renderPM path -- that needs a
    native rlPyCairo/PIL backend this environment doesn't have, and
    isn't needed for a vector format like PDF anyway, confirmed directly).
    """
    import verovio
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPDF
    from reportlab.pdfgen import canvas as pdf_canvas

    # Real, confirmed root cause of "Verovio couldn't parse" recurring
    # across arbitrary, otherwise-completely-normal pieces, found from the
    # deployed app's own server logs (not guessed): Verovio's bundled font/
    # glyph data (Bravura, Leipzig -- what it needs to even initialize,
    # before parsing a single note) wasn't being found at all on Streamlit
    # Cloud -- "Bravura font could not be loaded" / "The data cannot be
    # loaded because the font resources are not available". This has
    # nothing to do with any specific piece; every single call was failing
    # at toolkit startup, which is exactly why it looked like "many
    # different pieces" all failing the same way with an empty log, and
    # why nothing ever reproduced locally (this machine's own verovio
    # install has its data files in the right place; whatever Streamlit
    # Cloud's installer -- uv, per its own build log -- did with the pip
    # package's bundled data apparently didn't carry them over correctly).
    # verovio.__init__.py normally sets this itself via
    # importlib.resources.files('verovio')/'data' at import time, but that
    # depends on the installed package actually having its data files
    # where it expects -- fixed by bundling our OWN copy of that same data
    # directory in this repo (verovio_data/, ~6MB) and pointing Verovio at
    # it explicitly, removing the dependency on the installer getting the
    # pip package's own data bundling right.
    verovio.setDefaultResourcePath(str(Path(__file__).parent / 'verovio_data'))

    xml_bytes = score_to_download_bytes(score)
    tk = verovio.toolkit()
    if not tk.loadData(xml_bytes.decode('utf-8')):
        # tk.getLog() carries Verovio's own reason (e.g. which element it
        # choked on) -- surfacing it here so a failure is diagnosable from
        # the error message alone, rather than needing another round of
        # blind reproduction like the earlier zero-duration LilyPond bug.
        log = tk.getLog().strip()
        raise RuntimeError(
            "Verovio couldn't parse this piece's MusicXML" + (f": {log}" if log else ".")
        )
    # adjustPageHeight only -- Verovio's own default page width/scale (A4-
    # proportioned, 100%) is what's actually well-tuned for a normal-looking
    # printed page. An earlier version overrode both (pageWidth 1500, scale
    # 40, picked without a real visual check at the time) -- confirmed
    # directly to be the cause of the "huge edge" complaint: at that scale,
    # content occupies a small fraction of the page relative to its margins.
    tk.setOptions({"adjustPageHeight": True})
    page_count = tk.getPageCount()

    buffer = io.BytesIO()
    canvas = None
    for page in range(1, page_count + 1):
        svg = _flatten_svg_text_elements(tk.renderToSVG(page).encode('utf-8'))
        drawing = svg2rlg(io.BytesIO(svg))
        if canvas is None:
            canvas = pdf_canvas.Canvas(buffer, pagesize=(drawing.width, drawing.height))
        else:
            canvas.setPageSize((drawing.width, drawing.height))
        renderPDF.draw(drawing, canvas, 0, 0)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


_SVG_NS = 'http://www.w3.org/2000/svg'


def _flatten_svg_text_elements(svg_bytes):
    """Verovio nests <tspan> elements for styled text (page title, part
    labels, cadence/point-of-imitation/homorhythm labels) several levels
    deep, and -- confirmed directly, not assumed -- puts the actual x/y/
    text-anchor position on the FIRST NESTED <tspan> rather than the outer
    <text> element itself whenever that text has its own inner styling
    (the page title is the clearest example: <text font-size="0px"> with
    no position of its own, wrapping a positioned <tspan>). svglib reads
    position only from the outer <text> tag and silently defaults to
    (0, 0) when it isn't there -- confirmed by inspecting the resulting
    reportlab String objects directly: the title collapsed to x=0,y=0
    while simpler single-level labels (e.g. "Soprano") kept their real
    position. That's exactly what "the title isn't centered, just a few
    letters on the left" was: a title anchored "middle" at the literal
    left edge of the page, not a genuine layout/centering bug in Verovio
    itself (its own SVG marks the title text-anchor="middle" at the
    correct x correctly).

    Rewrites every <text> element into the same simple, single-level
    shape svglib already handles correctly: one <tspan> holding all of
    that element's visible text (concatenated from whatever depth it was
    at), with position/anchor promoted from wherever they actually were
    (the <text> itself, or its first positioned descendant <tspan>) onto
    the <text> element directly. Also drops each <title> sub-element's
    own text (an accessibness label Verovio embeds for screen readers,
    not visible content -- concatenating it in would have prepended the
    literal word "title" to on-page text).
    """
    ET.register_namespace('', _SVG_NS)
    root = ET.fromstring(svg_bytes)
    for text_el in root.iter(f'{{{_SVG_NS}}}text'):
        pos_source = text_el
        if not any(k in text_el.attrib for k in ('x', 'y', 'text-anchor')):
            for tspan in text_el.iter(f'{{{_SVG_NS}}}tspan'):
                if any(k in tspan.attrib for k in ('x', 'y', 'text-anchor')):
                    pos_source = tspan
                    break
        x, y = pos_source.attrib.get('x'), pos_source.attrib.get('y')
        anchor = pos_source.attrib.get('text-anchor')

        content = ''.join(
            (node.text or '') for node in text_el.iter(f'{{{_SVG_NS}}}tspan')
        ).strip()
        font_size = None
        for tspan in text_el.iter(f'{{{_SVG_NS}}}tspan'):
            if 'font-size' in tspan.attrib:
                font_size = tspan.attrib['font-size']

        for child in list(text_el):
            text_el.remove(child)
        text_el.text = None
        if x:
            text_el.set('x', x)
        if y:
            text_el.set('y', y)
        if anchor:
            text_el.set('text-anchor', anchor)
        new_tspan = ET.SubElement(text_el, f'{{{_SVG_NS}}}tspan')
        if font_size:
            new_tspan.set('font-size', font_size)
        new_tspan.text = content
    return ET.tostring(root)


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


def show_result(annotated_score, stats, filename_stem, include_cadences=False, include_ptypes=False, include_homorhythm=False, key_prefix=None):
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
    key_prefix disambiguates this call's own widget keys (the PDF build
    button) when the same filename_stem could render from more than one
    tab at once -- same collision this app already guards against
    elsewhere (see render_preview_and_annotate's docstring); defaults to
    filename_stem itself when the caller doesn't have a more specific one
    (e.g. the Upload tab, where only one instance ever exists).
    """
    key_prefix = key_prefix or filename_stem
    # Shown unconditionally, before anything analysis-specific -- which
    # print edition (or, for CRIM, which modern critical edition and
    # editorial team) an encoding derives from matters for Renaissance
    # music specifically (different editors made different musica ficta/
    # barring choices for the "same" piece), and matters whether or not
    # any analysis was even requested. See _humdrum_edition_info/
    # _crim_edition_info for exactly what's shown and why no single
    # blanket claim about "the edition" would be honest here.
    edition = stats.get('edition')
    if edition:
        st.caption("📖 Source edition, from this piece's own encoded metadata:")
        for label, value in edition:
            st.caption(f"**{label}:** {value}")

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

    # Quick numeric summary before the detailed strip plot below -- see
    # _add_density()'s own docstring for exactly what this measures
    # (fraction of measures touched, not a raw event count) and why.
    # Ordered Cadence/Points of Imitation/Homorhythm regardless of dict
    # insertion order, which depends on which analyses were requested.
    density = stats.get('density')
    if density:
        present = [label for label in ('Cadence', 'Points of Imitation', 'Homorhythm') if label in density]
        cols = st.columns(len(present))
        for col, label in zip(cols, present):
            col.metric(f"{label} density", f"{density[label]:.0%}")

    # One row per detected event across whichever analyses ran -- see
    # _append_timeline(). Only present when `annotated` is True (nothing
    # is ever appended for an empty/unrequested analysis), so no separate
    # guard is needed beyond `if timeline`.
    timeline = stats.get('timeline')
    if timeline:
        st.caption("Where these occur across the piece, by measure number:")
        st.scatter_chart(pd.DataFrame(timeline), x='Measure', y='Type', color='color', height=200)

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
        type="primary",
    )

    # PDF, via Verovio -- see score_to_pdf_bytes()'s own docstring. Built
    # only on an explicit click (not automatically alongside the MusicXML
    # above), and cached in session_state once built so a later, unrelated
    # rerun doesn't recompute it.
    #
    # pdf_cache_key MUST include filename_stem, not just key_prefix -- a
    # real bug, not a hypothetical: an earlier version keyed this by
    # key_prefix alone (e.g. just "browse"), which is shared by every piece
    # reachable from that same tab. Analyzing piece A, then switching to
    # piece B in the same tab's "Pick one" dropdown and clicking Analyze
    # again, found piece A's cache entry already present and reused IT --
    # including a cached error -- for piece B, with no recomputation at
    # all. That's confirmed to be the actual explanation for a report of
    # "Verovio couldn't parse" recurring across several different pieces:
    # it was one real (or spurious) failure on the FIRST piece, replayed
    # unchanged for everything analyzed afterward in that tab session, not
    # independent failures on each piece -- a from-scratch reproduction of
    # each reported piece in isolation never failed. filename_stem is
    # already unique per piece (see result_key above, same pattern).
    pdf_cache_key = f"{key_prefix}_{filename_stem}_pdf"
    # The trigger button only renders before it's been built -- once cached,
    # only the real download button (or the error) shows, so a successful
    # build doesn't leave a redundant "Download PDF" sitting above the
    # actual download button.
    if pdf_cache_key not in st.session_state:
        if st.button(
            "📄 Download annotated PDF" if annotated else "📄 Download PDF",
            key=f"{pdf_cache_key}_build",
            type="primary",
        ):
            with st.spinner(_random_loading_message()):
                try:
                    st.session_state[pdf_cache_key] = {'bytes': score_to_pdf_bytes(annotated_score), 'error': None}
                except Exception as exc:
                    st.session_state[pdf_cache_key] = {'bytes': None, 'error': str(exc)}

    if pdf_cache_key in st.session_state:
        cached_pdf = st.session_state[pdf_cache_key]
        if cached_pdf['error']:
            st.error(
                f"Couldn't render a PDF for this piece ({cached_pdf['error']}). "
                "The MusicXML download above is unaffected -- open it directly "
                "in MuseScore, Finale, or Dorico instead."
            )
        else:
            st.download_button(
                "Download annotated PDF" if annotated else "Download PDF",
                data=cached_pdf['bytes'],
                file_name=f"{filename_stem}_annotated.pdf" if annotated else f"{filename_stem}.pdf",
                mime="application/pdf",
                key=f"{pdf_cache_key}_download",
                type="primary",
            )


with st.expander("ℹ️ Credits & data sources"):
    st.markdown(
        """
**None of the structural analysis here is this app's own work.** Every
cadence, point of imitation, and homorhythmic passage this tool marks
comes from calling [CRIM Intervals](https://github.com/HCDigitalScholarship/intervals)'s
own `cadences()`, `presentationTypes()`, and `homorhythm()` methods
directly -- built by Richard Freedman (Haverford College) and the
[CRIM Project](https://crimproject.org/) team, licensed
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). This
app is an independent, unaffiliated project built on top of that
library -- it isn't CRIM's own official web app (that's
[crimintervals.streamlit.app](https://crimintervals.streamlit.app/), a
separate tool by the CRIM team themselves). Scores are parsed with
[music21](https://www.music21.org/) (Cuthbert & Ariza;
[BSD-3-Clause](https://github.com/cuthbertLab/music21/blob/master/LICENSE)).

**The 7 data sources**, with the terms each one actually publishes:
- **music21-bundled corpus** (Palestrina, Monteverdi) -- ships inside music21 itself
- **[CRIM Project](https://crimproject.org/)** -- see CRIM Intervals' license above
- **[Josquin Research Project](https://github.com/josquin-research-project/jrp-scores)** --
  [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)
- **[The 1520s Project](https://github.com/benory/1520s-project-scores)** --
  [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)
- **[Tasso in Music Project](https://github.com/TassoInMusicProject/tasso-scores)** --
  no license file published as of this writing
- **[SEILS](https://github.com/SEILSdataset/SEILSdataset)** --
  [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)
- **[Lassus's Geistliche Psalmen](https://github.com/WolfgangDrescher/lassus-geistliche-psalmen)** --
  no license file published as of this writing

This app itself is free, non-commercial, and unaffiliated with any of
the projects above -- consistent with every non-commercial term listed.
If you're citing or reusing results from a specific piece, cite that
piece's own source collection (linked above), not just this app. Full
source code: [github.com/vclag/Renaissance-score-workbench](https://github.com/vclag/Renaissance-score-workbench).

**Citing this app itself:** if you use it in published work -- beyond
citing CRIM Intervals/music21 above for the actual analysis methods --
this repo has a `CITATION.cff` file; GitHub renders a "Cite this
repository" button from it (APA/BibTeX) in the repo's own sidebar.

This repo's own source code (not the musical data it aggregates, nor
the third-party libraries above -- several of which are more
restrictive) is [MIT-licensed](https://github.com/vclag/Renaissance-score-workbench/blob/master/LICENSE).
        """
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

**References** -- the categories above aren't this app's own invention, or
even CRIM's: the CVF names/roles are reproduced from
[CRIM Intervals' own documentation](https://github.com/HCDigitalScholarship/intervals)
(`ImportedPiece.cadences`/`.cvfs` docstrings), so this stays accurate if
CRIM's own definitions change -- but CRIM itself is applying an existing
musicological framework, not one it invented:
- The Cantizans/Tenorizans/Bassizans/Altizans names are Bernhard Meier's
  own Latin coinages for these voice-leading roles: Bernhard Meier, *Die
  Tonarten der klassischen Vokalpolyphonie* (Utrecht: A. Oosthoek, 1974);
  trans. Ellen S. Beebe, rev. by the author, as *The Modes of Classical
  Vocal Polyphony: Described According to the Sources* (New York: Broude
  Brothers, 1988).
- The systematic classification into named cadence types (Authentic,
  Phrygian, Clausula Vera, etc.) that CRIM's algorithm implements is
  documented in Alexander Morgan, Daniel Russo-Batterham, and Richard
  Freedman, ["Musicologists and Data Scientists Pull out all the Stops:
  Defining Renaissance Cadences Systematically"](https://www.academia.edu/109443988/Musicologists_and_Data_Scientists_Pull_out_all_the_Stops_Defining_Renaissance_Cadences_Systematically)
  (Music Encoding Conference, Halifax, Canada, 2022).
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

**Reference:** this pairwise, interval-progression approach to detecting
cadences computationally is documented in Alexander Morgan, Daniel
Russo-Batterham, and Richard Freedman, ["Musicologists and Data Scientists
Pull out all the Stops: Defining Renaissance Cadences Systematically"](https://www.academia.edu/109443988/Musicologists_and_Data_Scientists_Pull_out_all_the_Stops_Defining_Renaissance_Cadences_Systematically)
(Music Encoding Conference, Halifax, Canada, 2022) -- same paper as the
cadence-labels expander above, since it covers both the categories and
the detection method together.
        """
    )

with st.expander("What do the points-of-imitation labels mean?"):
    st.markdown(
        """
This is a separate feature from cadences -- turn it on with the "Mark
points of imitation" checkbox next to Analyze/Download. It runs CRIM's
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

**References** -- "presentation types" and this specific three-way split
(Point of Entry / Imitative Duo / Fuga) are Peter Schubert's own
terminology, not CRIM's: Peter N. Schubert, *Modal Counterpoint,
Renaissance Style* (New York: Oxford University Press, 1999). See also
Julie E. Cumming and Peter Schubert, "The Origins of Pervasive
Imitation," in *The Cambridge History of Fifteenth-Century Music*, ed.
Anna Maria Busse Berger and Jesse Rodin (Cambridge: Cambridge University
Press, 2015), chapter 12, for how this fits into the broader history of
imitative texture in the repertoire. CRIM's own `presentationTypes()` is
its computational implementation of Schubert's categories, not a new
taxonomy of its own -- see [CRIM Intervals' own documentation](https://github.com/HCDigitalScholarship/intervals)
for the code side of that.
        """
    )

with st.expander("What do the homorhythm labels mean?"):
    st.markdown(
        """
A third, separate feature -- turn it on with the "Mark homorhythmic
passages" checkbox next to Analyze/Download. It runs CRIM's `homorhythm()`,
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

**Reference:** unlike cadences (Meier) or presentation types (Schubert),
"homorhythm" itself isn't a single scholar's coinage -- it's standard,
widely-used musicological terminology for chordal, same-rhythm textures,
so there's no one naming source to point to the way the other two
expanders do. CRIM's own rhythm+lyrics n-gram method for detecting it
computationally is part of the same project documented in Richard
Freedman and David Fiala, ["Citations: The Renaissance Imitation Mass
Project (CRIM)"](https://online.ucpress.edu/jams/article/77/3/863/203475/Citations-The-Renaissance-Imitation-Mass-Project),
*Journal of the American Musicological Society* 77, no. 3 (2024): 863-875.
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
        stats = {}
        edition = _crim_edition_info(mei_url)
        if edition:
            stats['edition'] = edition
        return piece.score, stats, None

    annotated_score, stats = piece.score, {}
    if include_cadences:
        cadences, error = _safe_cadences(piece)
        if error:
            return None, None, error
        if not cadences.empty:
            annotated_score, stats = annotate_score(piece.score, cadences)
            _append_timeline(stats, cadences['Measure'], 'Cadence', CADENCE_COLOR)
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
            first_entry_measures = ptypes['Measures_Beats'].apply(
                lambda mb: int(float(mb[0].split('/')[0]))
            )
            _append_timeline(stats, first_entry_measures, 'Points of Imitation', PRESENTATION_COLOR)
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
            _append_timeline(stats, hr.index.get_level_values('Measure'), 'Homorhythm', HOMORHYTHM_COLOR)
    _add_density(stats, annotated_score)
    edition = _crim_edition_info(mei_url)
    if edition:
        stats['edition'] = edition
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


# Human-readable names for the raw `collection` key -- used only for the
# corpus-overview CSV's display_name column below, purely a readability
# nicety on a data export. NOT reintroducing a Collection filter widget
# (that was tried and reverted earlier for being redundant with the
# per-collection tabs) -- a CSV column is a different, lower-stakes use.
COLLECTION_DISPLAY_NAMES = {
    'music21': 'music21 corpus', 'crim': 'CRIM Project',
    'jrp': 'Josquin Research Project', '1520s': '1520s Project',
    'tasso': 'Tasso in Music Project', 'seils': 'SEILS', 'lassus_psalms': 'Lassus Psalms',
}


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
        if not p['mei_links']:
            # A real gap in CRIM's own catalog (confirmed directly) --
            # this piece is listed but has no MEI file at all yet.
            return voices, None, "CRIM has no MEI file for this piece yet -- can't check its text/lyrics or annotate it."
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

    Two fixes on top of that raw split, both reused from elsewhere in
    this file rather than left half-applied here:
    - lassus_psalms's own labels are just a psalm title with no
      'Composer — Title' prefix at all (see fetch_lassus_psalms_pieces
      -- single-composer collection, nothing to split on), so the
      generic branch below would wrongly treat the whole title as the
      composer -- confirmed directly, e.g. "Beatus vir" ending up as a
      50-row-strong fake "composer". Matches CRIM's own spelling for the
      same person ("Roland de Lassus" -- confirmed against this app's
      real composer data) so the two collections' counts merge instead
      of colliding.
    - A trailing '(YYYY)' (Tasso's own convention, see
      _composer_from_collection_label) is stripped here too -- that
      function only ever ran on the per-collection filter dropdown, not
      this CSV/word-cloud path, so Tasso's composer counts here were
      still fragmenting by publication year (confirmed directly: e.g.
      five separate "Cifra (....)" rows in the real composer-count CSV,
      all the same person). Same regex, same reasoning, just applied
      here too.
    """
    if collection == 'music21':
        composer = label.split('] ', 1)[0].lstrip('[')
    elif collection == 'lassus_psalms':
        composer = 'Roland de Lassus'
    else:
        inner = label.split('] ', 1)[1] if '] ' in label else label
        composer = inner.partition(' — ')[0]
    composer = re.sub(r'\s*\(\d{4}\)$', '', composer)

    row = {'collection': collection, 'composer': composer, 'label': label,
           'source_url': '', 'music21_corpus_path': ''}
    if collection == 'music21':
        corpus_key, piece_id = native_ref
        row['music21_corpus_path'] = f'{corpus_key}/{piece_id}'
    elif collection == 'crim':
        # A real gap in CRIM's own catalog, not something wrong with this
        # app: some pieces are listed with an empty mei_links list at all
        # (no MEI file exists for them yet on CRIM's side) -- confirmed
        # directly, e.g. 20 of the 52 "Palestrina" CRIM matches, including
        # "Missa Io mi son giovinetta" and "Missa Gabriel archangelus".
        # source_url is left blank for these rather than crashing the
        # whole export over one row.
        if native_ref['mei_links']:
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


_COMPOSER_WORDCLOUD_ALIASES = {
    # Same real person, a genuinely different name form -- not fixable
    # by the comma-flip in _normalize_composer_for_wordcloud() below.
    # Checked directly against this app's real composer-count data
    # before adding either entry (fetched live: 1318 music21 pieces
    # under "Palestrina", 52 CRIM pieces under the full name; 476 JRP
    # pieces under one Josquin capitalization, 5-6 CRIM under the
    # other) -- not guessed from the names alone. Deliberately short:
    # other real variants turned up in that same check (e.g. "Jean
    # Richafort" vs "Johannes Richafort" post-flip -- French vs
    # Latinized given name, 68 vs 1 pieces) were left out rather than
    # guessed at, since a wrong merge here is worse than a small
    # duplicate word.
    'Giovanni Pierluigi da Palestrina': 'Palestrina',
    'Josquin Des Prez': 'Josquin des Prez',
}


def _normalize_composer_for_wordcloud(composer):
    """Folds known same-composer name variants together, for the word
    cloud ONLY -- the per-collection composer-count CSV deliberately
    stays raw and unmerged (see its own help= text: it's meant to be a
    precise count, and composer spelling isn't consistent between
    collections; this function's job is a fairer illustrative picture,
    not a corrected precise count).

    Two passes:
    1. JRP's own metadata (and a few CRIM entries) name composers
       "Lastname, Firstname" -- every other collection uses "Firstname
       Lastname". Flipping any comma-containing name to that order is
       safe here: checked directly against every one of the 25 comma-
       containing composer strings in this app's real corpus before
       relying on it (all JRP/CRIM, all genuinely "Last, First"), not
       assumed from the convention alone -- worth re-checking if a
       collection using a comma for some other reason is ever added.
    2. _COMPOSER_WORDCLOUD_ALIASES (above) for the remaining cases the
       flip can't reach -- genuinely different name forms, not just
       reordered.
    """
    if ',' in composer:
        last, _, first = composer.partition(',')
        composer = f"{first.strip()} {last.strip()}"
    return _COMPOSER_WORDCLOUD_ALIASES.get(composer, composer)


def _composer_wordcloud_png_bytes(index):
    """A composer word cloud across the WHOLE corpus, sized by piece
    count -- reuses _browse_row_to_csv_dict's own composer extraction,
    same as the composer-count CSV, then folds known same-composer name
    variants together via _normalize_composer_for_wordcloud() (see that
    function for exactly what is and isn't merged, and why). Unlike the
    composer-count CSV, this also merges composers across all 7
    collections into one visual rather than breaking them out per-
    collection -- a rarer, unverified spelling variant can still show up
    as two words here, which a purely illustrative overview can tolerate
    in a way a precise per-collection count table shouldn't."""
    counts = Counter(
        _normalize_composer_for_wordcloud(_browse_row_to_csv_dict(row[0], row[1], row[2])['composer'])
        for row in index
    )
    wc = WordCloud(width=900, height=380, background_color=None, mode='RGBA', colormap='plasma')
    wc.generate_from_frequencies(counts)
    buf = io.BytesIO()
    wc.to_image().save(buf, format='PNG')
    return buf.getvalue()


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
        # A real gap in CRIM's own catalog (confirmed directly, see
        # _browse_row_to_csv_dict's comment) -- some pieces are listed
        # with an empty mei_links list, no MEI file available at all.
        # Caught here with a clear, specific message rather than letting
        # native_ref['mei_links'][0] raise an unhelpful IndexError that
        # the caller's generic exception handler would report as just
        # "list index out of range."
        if not native_ref['mei_links']:
            return None, None, "CRIM has this piece catalogued but no MEI file for it yet (a gap in CRIM's own data, not a problem with your search)."
        return _annotate_crim_piece(
            native_ref['mei_links'][0], include_cadences=include_cadences,
            include_ptypes=include_ptypes, include_homorhythm=include_homorhythm,
        )
    raw_url = KERN_COLLECTION_BASE_URLS[collection] + native_ref
    return _annotate_kern_from_url(
        raw_url, Path(native_ref).stem, include_cadences=include_cadences,
        include_ptypes=include_ptypes, include_homorhythm=include_homorhythm,
    )


def _import_piece_by_collection(collection, native_ref):
    """Fetches and imports a piece as a crim_intervals ImportedPiece, for
    the bulk analysis-table export below -- mirrors annotate_by_
    collection's exact per-collection fetch mechanics (music21 corpus
    parse, CRIM's own MEI-url import, Humdrum kern fetch+parse) but
    stops there: no score mutation, no annotate_score/_presentation_
    types/_homorhythm call. A raw-data export wants CRIM's own
    DataFrame (cadences()/presentationTypes()/homorhythm()) straight
    from a piece object, not an annotated score -- running the
    annotation step too would just waste time coloring/labeling a score
    nothing here ever reads. Returns (piece, error)."""
    if collection == 'music21':
        corpus_key, piece_id = native_ref
        score = m21.corpus.parse(f"{corpus_key}/{piece_id}")
        return ci.main_objs.ImportedPiece(score, piece_id), None
    if collection == 'crim':
        if not native_ref['mei_links']:
            return None, "CRIM has this piece catalogued but no MEI file for it yet (a gap in CRIM's own data, not a problem with your search)."
        piece = ci.importScore(native_ref['mei_links'][0])
        if piece is None:
            return None, "CRIM couldn't import this piece (bad MEI file or network issue)."
        return piece, None
    raw_url = KERN_COLLECTION_BASE_URLS[collection] + native_ref
    kern_text = requests.get(raw_url, timeout=20).text
    score = m21.converter.parse(kern_text)
    return ci.main_objs.ImportedPiece(score, Path(native_ref).stem), None


# Hard cap on Browse's bulk CRIM analysis-table export -- deliberately
# tighter than BULK_ZIP_MAX_MATCHES below: that cap was set from a real
# benchmark of fetch+parse+convert ALONE (no CRIM computation). This
# feature runs actual cadences()/presentationTypes()/homorhythm() on top
# of that same fetch, which is real, uncharacterized extra cost per
# piece -- capped conservatively until it's been benchmarked live on the
# deployed app (not guessed at), same discipline as the ZIP cap
# originally came from. Revisit once real timing is in.
BULK_ANALYSIS_MAX_MATCHES = 15

# Tighter than BULK_ANALYSIS_MAX_MATCHES: a PDF isn't just CRIM analysis,
# it's analysis PLUS a real Verovio render + reportlab page-assembly per
# piece (measured directly on a single real piece earlier this project:
# ~5-6s for a modest 4-page score, more for larger ones) -- meaningfully
# heavier than the CSV export's per-piece cost, which is just dataframe
# work. Also provisional/not benchmarked live at this exact number, same
# honesty as the CSV cap above.
BULK_PDF_ZIP_MAX_MATCHES = 10


def _bulk_pdf_zip_bytes(matches, include_cadences, include_ptypes, include_homorhythm, progress_callback=None):
    """Runs the checked analyses on every match (same annotate_by_collection
    call the CSV export and single-piece Analyze both already use) and
    renders each one to an annotated PDF via score_to_pdf_bytes -- the same
    function and same Verovio path the single-piece "Download PDF" button
    uses, just looped and zipped instead of offered one at a time. Returns
    (zip_bytes, failed) where `failed` is a list of (label, reason) for any
    piece that couldn't be fetched/analyzed/rendered -- skipped rather than
    aborting the whole batch, same convention as _bulk_zip_bytes/
    _bulk_analysis_csv_bytes. progress_callback(index, total, label), if
    given, is called right before each piece starts."""
    buf = io.BytesIO()
    failed = []
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, (label, collection, native_ref) in enumerate(matches):
            if progress_callback:
                progress_callback(i, len(matches), label)
            try:
                score, _stats, error = annotate_by_collection(
                    collection, native_ref, include_cadences=include_cadences,
                    include_ptypes=include_ptypes, include_homorhythm=include_homorhythm,
                )
                if error:
                    raise RuntimeError(error)
                pdf_bytes = score_to_pdf_bytes(score)
            except Exception as e:
                failed.append((label, str(e)))
                continue
            stem = _browse_piece_filename_stem(collection, native_ref)
            annotated = include_cadences or include_ptypes or include_homorhythm
            zf.writestr(f'{stem}_annotated.pdf' if annotated else f'{stem}.pdf', pdf_bytes)
    return buf.getvalue(), failed


def _bulk_analysis_csv_bytes(matches, include_cadences, include_ptypes, include_homorhythm, progress_callback=None):
    """Runs whichever of CRIM's cadences()/presentationTypes()/
    homorhythm() are requested across every match, concatenating each
    analysis's own raw DataFrame across all pieces into one CSV per
    analysis type -- CRIM's own rich columns (CadType/Tone/RelTone/CVFs
    for cadences; Presentation_Type/Soggetti/Voices for presentation
    types; hr_voices/active_voices for homorhythm), not just a summary
    count, so the result is ready for someone's own stats in pandas/R/
    whatever, the same "hand over the data, not just a picture of it"
    reasoning as the plain CSV/ZIP exports above. Three 'collection'/
    'composer'/'label' columns are prepended to every row so pieces
    stay identifiable once concatenated.

    Returns (csv_bytes_by_analysis, failed) -- csv_bytes_by_analysis is
    a dict with only the keys among 'cadences'/'presentation_types'/
    'homorhythm'/'density' that were both requested AND produced at
    least one row anywhere in the batch (an analysis requested but
    found nowhere just doesn't appear, rather than handing back an
    empty file). failed is a list of (label, reason) for any piece that
    couldn't be fetched/imported/analyzed -- skipped rather than
    aborting the whole batch, reported rather than silently missing.

    'density' is a separate, one-row-per-piece summary CSV (not one row
    per event like the other three) -- the same per-piece density
    figure show_result() shows for a single piece (see _add_density's
    own docstring for exactly what it measures and why: fraction of
    measures touched, not a raw event count), collected across every
    piece in this batch so they're directly comparable side by side --
    the actual point of a bulk export, per direct feedback, that the
    raw per-event CSVs above don't serve on their own. A density of 0
    for a requested-but-empty analysis is a real, meaningful data point
    (kept as 0, not blank); a blank cell means that analysis wasn't
    successfully computed for that piece at all (e.g. ptypes failed).

    List-valued columns crim_intervals itself produces (e.g.
    presentationTypes()' Measures_Beats/Voices/Soggetti/Offsets) come
    out as Python-list-literal text in the CSV (pandas' own
    to_csv/str() behavior, not something this function reformats) --
    re-parse with e.g. ast.literal_eval if you need them as real lists,
    same caveat as reading any DataFrame column of list objects back
    out of a CSV."""
    cadence_frames, ptype_frames, hr_frames = [], [], []
    density_rows = []
    failed = []
    for i, (label, collection, native_ref) in enumerate(matches):
        if progress_callback:
            progress_callback(i, len(matches), label)
        try:
            piece, error = _import_piece_by_collection(collection, native_ref)
            if error:
                raise RuntimeError(error)
            tag = _browse_row_to_csv_dict(label, collection, native_ref)
            tag_cols = {'collection': tag['collection'], 'composer': tag['composer'], 'label': tag['label']}
            # {type_label: set of measures touched} for whichever analyses
            # were actually ATTEMPTED for this piece (present with an
            # empty set if attempted-but-found-nothing -- a real density
            # of 0, not a gap; absent entirely if never requested, or
            # requested but failed -- both correctly left blank in the
            # output row below, since neither is real data).
            touched = {}

            if include_cadences:
                cadences, cad_error = _safe_cadences(piece)
                if cad_error:
                    raise RuntimeError(cad_error)
                if not cadences.empty:
                    # PartMap (voice_detail=True, needed elsewhere for
                    # annotate_score's placement logic) holds actual
                    # music21 Note objects per voice, not exportable
                    # data -- dropped here rather than serialized into
                    # unreadable object-repr text. reset_index() (no
                    # drop=True) rather than discarding the index: it's
                    # not one of cadences()'s own documented columns,
                    # but it's the piece's raw offset -- not confirmed
                    # redundant with Measure/Beat/Progress, so kept
                    # rather than silently thrown away.
                    df = cadences.reset_index().drop(columns=['PartMap'], errors='ignore')
                    for col, val in reversed(tag_cols.items()):
                        df.insert(0, col, val)
                    cadence_frames.append(df)
                    touched['Cadence'] = set(cadences['Measure'])
                else:
                    touched['Cadence'] = set()

            if include_ptypes:
                # Guarded individually, same convention as run_pipeline:
                # a ptypes-specific failure just skips ptypes for this
                # piece, it doesn't invalidate the cadences row already
                # appended above -- catching it at the outer try/except
                # instead would wrongly mark this whole piece 'failed'
                # over one optional analysis.
                try:
                    ptypes = piece.presentationTypes()
                except Exception:
                    ptypes = None
                if ptypes is not None and not ptypes.empty:
                    df = ptypes.reset_index(drop=True)
                    for col, val in reversed(tag_cols.items()):
                        df.insert(0, col, val)
                    ptype_frames.append(df)
                    first_entry_measures = ptypes['Measures_Beats'].apply(
                        lambda mb: int(float(mb[0].split('/')[0]))
                    )
                    touched['Points of Imitation'] = set(first_entry_measures)
                elif ptypes is not None:
                    touched['Points of Imitation'] = set()

            if include_homorhythm:
                try:
                    hr = piece.homorhythm()
                except Exception:
                    hr = None
                if hr is not None and not hr.empty:
                    df = hr.reset_index()  # Measure/Beat/Offset are index levels here, not columns -- see _append_timeline
                    for col, val in reversed(tag_cols.items()):
                        df.insert(0, col, val)
                    hr_frames.append(df)
                    touched['Homorhythm'] = set(hr.index.get_level_values('Measure'))
                elif hr is not None:
                    touched['Homorhythm'] = set()

            if touched:
                total = _total_measures(piece.score)
                row = dict(tag_cols)
                for type_label in ('Cadence', 'Points of Imitation', 'Homorhythm'):
                    if type_label in touched:
                        row[f'{type_label} density'] = (len(touched[type_label]) / total) if total else None
                density_rows.append(row)
        except Exception as e:
            failed.append((label, str(e)))

    csv_bytes_by_analysis = {}
    if cadence_frames:
        csv_bytes_by_analysis['cadences'] = pd.concat(cadence_frames, ignore_index=True).to_csv(index=False).encode('utf-8')
    if ptype_frames:
        csv_bytes_by_analysis['presentation_types'] = pd.concat(ptype_frames, ignore_index=True).to_csv(index=False).encode('utf-8')
    if hr_frames:
        csv_bytes_by_analysis['homorhythm'] = pd.concat(hr_frames, ignore_index=True).to_csv(index=False).encode('utf-8')
    if density_rows:
        csv_bytes_by_analysis['density'] = pd.DataFrame(density_rows).to_csv(index=False).encode('utf-8')
    return csv_bytes_by_analysis, failed


# (download button label, file name) per key _bulk_analysis_csv_bytes can
# return -- shared by the loop that renders whichever download buttons apply.
BULK_ANALYSIS_DOWNLOAD_META = {
    'cadences': ("📄 Download cadence data CSV", "browse_cadences_bulk.csv"),
    'presentation_types': ("📄 Download points-of-imitation data CSV", "browse_presentation_types_bulk.csv"),
    'homorhythm': ("📄 Download homorhythm data CSV", "browse_homorhythm_bulk.csv"),
    'density': ("📊 Download per-piece density comparison CSV", "browse_density_bulk.csv"),
}


# Hard cap on Browse's "download all matches as ZIP" -- benchmarked directly
# against 8 real JRP pieces (fetch + music21 parse + MusicXML conversion, no
# CRIM at all): 2.6s-7.8s each, 5.25s average. 30 pieces keeps the whole
# operation under ~3 minutes; past that, sitting through a single Streamlit
# progress bar is a bad way to wait, and the CSV export (instant, any size)
# is the better fit for a bigger result set anyway -- not a silent truncation,
# the UI refuses outright and says why (see tab_browse below).
BULK_ZIP_MAX_MATCHES = 30

# How many of Browse's matches populate the "Pick one" selectbox -- purely a
# UI-rendering concern (Streamlit itself handles large dropdowns fine with
# typeahead filtering), NOT a data limit: the CSV/ZIP exports always cover
# every match regardless of this cap, and this only bounds the picker widget.
BROWSE_PICKER_MAX_SHOWN = 200


def _bulk_zip_bytes(matches, include_cadences=False, include_ptypes=False, include_homorhythm=False, progress_callback=None):
    """Fetches + parses + converts every match to MusicXML and zips them
    into one in-memory archive. include_cadences/ptypes/homorhythm all
    default to False (the original behavior: no CRIM analysis at all,
    the same "all three flags off" path already used for single-piece
    unannotated downloads -- see run_pipeline's docstring), but Browse
    passes through whatever the shared analysis checkboxes are actually
    set to, same convention as _bulk_pdf_zip_bytes/_bulk_analysis_csv_bytes,
    so this ZIP can hold either plain or annotated scores depending on
    what's checked, not just plain ones. Returns (zip_bytes, failed) where
    `failed` is a list of (label, reason) for any piece that couldn't be
    fetched/parsed -- skipped rather than aborting the whole batch, but
    reported explicitly rather than silently missing from the zip.
    progress_callback(index, total, label), if given, is called right
    before each piece starts."""
    buf = io.BytesIO()
    failed = []
    annotated = include_cadences or include_ptypes or include_homorhythm
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, (label, collection, native_ref) in enumerate(matches):
            if progress_callback:
                progress_callback(i, len(matches), label)
            try:
                score, _stats, error = annotate_by_collection(
                    collection, native_ref, include_cadences=include_cadences,
                    include_ptypes=include_ptypes, include_homorhythm=include_homorhythm,
                )
                if error:
                    raise RuntimeError(error)
                xml_bytes = score_to_download_bytes(score)
            except Exception as e:
                failed.append((label, str(e)))
                continue
            stem = _browse_piece_filename_stem(collection, native_ref)
            zf.writestr(f'{stem}_annotated.xml' if annotated else f'{stem}.xml', xml_bytes)
    return buf.getvalue(), failed


# Flavor text for the Analyze/Download button's own spinner -- purely
# decorative, picked together with the user from a larger brainstormed
# list (the rest were deliberately cut, not forgotten -- see this
# project's own conversation history). Real music-theory/notation
# terms throughout, not generic "loading..." filler: counterpoint,
# meantone temperament (tempering the syntonic comma is literally how
# meantone works, not a generic "tuning" reference), mensural notation
# (ligatures, breves, perfect/imperfect mensuration), and solmization
# (the hexachord).
_ANALYZE_LOADING_MESSAGES = [
    "Untangling counterpoint...",
    "Splitting the comma...",
    "Untying the ligatures...",
    "Balancing the voices...",
    "Coloring the breves...",
    "Checking for parallel fifths...",
    "Negotiating the hexachord...",
    "Weighing the perfect and imperfect...",
]


def _random_loading_message():
    """One random line from _ANALYZE_LOADING_MESSAGES for the Analyze/
    Download button's own spinner -- a fresh pick per click via
    random.choice, not a fixed rotation: Streamlit reruns the whole
    script on every interaction (see this file's own module docstring),
    so there's no persistent state across reruns to cycle through
    anyway without extra machinery this doesn't need."""
    return random.choice(_ANALYZE_LOADING_MESSAGES)


# Separate, deliberately different pool for spinners where nothing is
# actually being analyzed yet -- just listed or fetched (Browse's index
# load, the bulk ZIP export). Reusing _ANALYZE_LOADING_MESSAGES here
# would misdescribe what's really happening -- "Untangling
# counterpoint..." while literally no CRIM analysis runs (the ZIP
# export deliberately, explicitly documents that it runs none) would
# undercut a real clarity choice made earlier in this project, not
# just be a harmless whimsy mismatch.
_FETCH_LOADING_MESSAGES = [
    "Gathering the partbooks...",
    "Fetching from the archives...",
    "Cataloguing the collections...",
    "Assembling the choirbooks...",
]


def _random_fetch_message():
    """Same idea as _random_loading_message(), for the fetching/
    cataloguing spinners instead of the analysis ones -- see
    _FETCH_LOADING_MESSAGES for why these are a separate pool."""
    return random.choice(_FETCH_LOADING_MESSAGES)


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
        "Mark points of imitation", key=f"ptypes_{key_prefix}",
    )
    include_homorhythm = st.checkbox(
        "Mark homorhythmic passages", key=f"hr_{key_prefix}",
    )
    # Label reflects what this button will actually do, not just what it
    # hands back at the end -- with at least one box checked, clicking it
    # runs real analysis and shows results (the strip plot, stats, methods
    # blurb) before the file; "Analyze" says that up front instead of
    # reading as a plain file-download that quietly does more. With
    # nothing checked, it genuinely is just a download -- no analysis
    # runs at all -- so it keeps that label instead.
    action_label = "Analyze" if (include_cadences or include_ptypes or include_homorhythm) else "Download"

    col1, col2 = st.columns(2)
    # type="primary" -- Streamlit's own accent-colored button style,
    # using .streamlit/config.toml's primaryColor (the same orange from
    # the identity-review mockup) directly, not a custom CSS override.
    if col1.button("Preview", key=f"preview_{key_prefix}", type="primary"):
        with st.spinner(_random_loading_message()):
            voices, has_text, note = preview_piece(collection, native_ref)
        st.write(f"**Voices:** {voices if voices is not None else 'unknown'}")
        has_text_display = 'yes' if has_text else ('no' if has_text is False else 'unknown')
        st.write(f"**Has encoded text/lyrics:** {has_text_display}")
        if note:
            st.caption(note)
    # Keyed by tab AND piece (not just key_prefix) so switching the "Pick
    # one" dropdown to a different piece without re-clicking Analyze doesn't
    # keep showing a stale result for the previous one.
    result_key = f"{key_prefix}_{filename_stem}_result"
    if col2.button(action_label, key=f"annotate_{key_prefix}", type="primary"):
        with st.spinner(_random_loading_message()):
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
            st.session_state.pop(result_key, None)
            st.error(error)
        else:
            st.session_state[result_key] = (annotated_score, stats, include_cadences, include_ptypes, include_homorhythm)

    # Rendered from session_state, OUTSIDE the button's own if-block --
    # Streamlit only returns True from a button on the exact rerun it was
    # clicked in, so a widget nested inside show_result() (the "Build PDF"
    # button) would otherwise make the *entire* result -- including itself --
    # vanish the moment it's clicked, since that rerun sees col2.button(...)
    # as False again. Confirmed live: this was the actual cause of "clicked
    # Build PDF and nothing happened," not a LilyPond/PDF-specific bug.
    if result_key in st.session_state:
        stored_score, stored_stats, stored_cad, stored_pt, stored_hr = st.session_state[result_key]
        show_result(
            stored_score, stored_stats, filename_stem, include_cadences=stored_cad,
            include_ptypes=stored_pt, include_homorhythm=stored_hr, key_prefix=key_prefix,
        )



# One-time flourish marking "intro/credits is over, the actual app
# starts here" -- deliberately placed ONCE, at this one real boundary,
# not scattered as a general-purpose divider elsewhere (a decorative
# element repeated throughout the page reads as clutter fast -- see
# this project's own conversation history on why the "featured piece"
# widget idea was dropped for exactly that risk). Reuses three of the
# same mensural notehead shapes as the favicon (breve/void semibreve/
# minim), not new art.
st.markdown(
    """
    <div style="display:flex; align-items:center; justify-content:center; gap:0.9rem; margin:1.6rem 0; color:#a8451f; opacity:0.85;">
        <div style="flex:1; max-width:90px; height:1px; background:#f0dfc4;"></div>
        <svg width="20" height="20" viewBox="0 0 40 24"><rect x="4" y="4" width="32" height="16" fill="currentColor"/></svg>
        <svg width="20" height="20" viewBox="0 0 32 24"><polygon points="16,3 28,12 16,21 4,12" fill="none" stroke="currentColor" stroke-width="3"/></svg>
        <svg width="20" height="20" viewBox="0 0 32 64"><polygon points="16,34 28,44 16,54 4,44" fill="currentColor"/><line x1="27" y1="44" x2="27" y2="4" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>
        <div style="flex:1; max-width:90px; height:1px; background:#f0dfc4;"></div>
    </div>
    """,
    unsafe_allow_html=True,
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
    # Eager, not button-gated: build_browse_index() is the same
    # @st.cache_data(ttl=3600)-wrapped call the search box below (and
    # every collection tab) already shares, so this doesn't add a new
    # expensive operation -- it just moves the first hit of it earlier,
    # so the CSV downloads below and the word cloud at the bottom of
    # this tab are both ready without a separate build step.
    with st.spinner(f"{_random_fetch_message()} (first visit this hour can take ~15s, "
                     "instant after)"):
        full_index = build_browse_index()

    with st.expander("📦 Download the whole corpus metadata, no search needed (all 7 collections)"):
        st.caption(
            "Not the scores themselves -- a manifest of every piece this app knows about, "
            "across all 7 collections, as one CSV -- same columns as a search result's own "
            "CSV export (collection, composer, label, source URL / music21 corpus path) -- "
            "plus a small per-collection piece-count table. To get actual scores for a set of "
            "pieces, search or filter down to them above and use that tab's ZIP download."
        )
        st.download_button(
            f"📄 Download all {len(full_index)} pieces as CSV",
            data=_matches_to_csv_bytes(full_index),
            file_name="full_corpus.csv",
            mime="text/csv",
            key="browse_full_csv_download",
            type="primary",
        )
        counts = Counter(row[1] for row in full_index)
        overview_buf = io.StringIO()
        writer = csv.writer(overview_buf)
        writer.writerow(['collection', 'display_name', 'piece_count'])
        for key in sorted(counts, key=lambda k: -counts[k]):
            writer.writerow([key, COLLECTION_DISPLAY_NAMES.get(key, key), counts[key]])
        st.download_button(
            "📊 Download per-collection piece counts as CSV",
            data=overview_buf.getvalue().encode('utf-8'),
            file_name="corpus_overview.csv",
            mime="text/csv",
            key="browse_overview_csv_download",
            type="primary",
        )

        # Per-COLLECTION composer counts -- not merged across
        # collections (composer naming isn't consistent across them,
        # see build_browse_index's docstring), just how many pieces
        # each composer has within their own collection. Reuses
        # _browse_row_to_csv_dict's own composer extraction (already
        # built and tested for the manifest CSV) rather than a third
        # copy of the same per-collection parsing logic.
        composer_counts = Counter(
            (row[1], _browse_row_to_csv_dict(row[0], row[1], row[2])['composer'])
            for row in full_index
        )
        composer_buf = io.StringIO()
        writer = csv.writer(composer_buf)
        writer.writerow(['collection', 'composer', 'piece_count'])
        for (collection, composer), count in sorted(composer_counts.items(), key=lambda kv: -kv[1]):
            writer.writerow([collection, composer, count])
        st.download_button(
            "🎼 Download per-collection composer counts as CSV",
            data=composer_buf.getvalue().encode('utf-8'),
            file_name="composer_overview.csv",
            mime="text/csv",
            key="browse_composer_overview_csv_download",
            type="primary",
            help="How many pieces each composer has WITHIN their own collection -- not merged "
                 "across collections, since composer spelling isn't consistent between them "
                 "(e.g. CRIM's 'Josquin Des Prez' vs JRP's 'Josquin des Prez').",
        )

    query = st.text_input(
        'Search (partial words OK)',
        key="browse_query",
        help='Matches any text in a result -- composer, title, movement, catalog year, anything '
             'shown. Words don\'t need to be whole or in order ("159" matches any 1590s piece), '
             'but every word you type must appear somewhere, so "josquin missa" finds every '
             'Josquin mass movement.',
    )

    if query:
        # Reuses full_index, already built above for the word cloud --
        # no second build_browse_index() call needed.
        index = full_index
        # Every space-separated word in the query must appear somewhere in
        # the label, in any order -- not one single substring match. "josquin
        # missa" used to match nothing at all (no label literally contains
        # that exact phrase); as two separate terms it correctly matches
        # every Josquin mass movement (confirmed directly: 0 -> 164 real
        # matches). A single-word query (e.g. "agnus", no composer) behaves
        # exactly as before -- this only adds power for multi-word queries.
        terms = query.lower().split()
        matches = [row for row in index if all(term in row[0].lower() for term in terms)]

        if not matches:
            st.info("No matches.")
        else:
            shown = matches[:BROWSE_PICKER_MAX_SHOWN]
            st.caption(
                f"{len(matches)} match(es)"
                + (f" -- showing first {BROWSE_PICKER_MAX_SHOWN}" if len(matches) > BROWSE_PICKER_MAX_SHOWN else "")
            )
            st.download_button(
                f"📄 Download all {len(matches)} match(es) as CSV",
                data=_matches_to_csv_bytes(matches),
                file_name="browse_results.csv",
                mime="text/csv",
                key="browse_csv_download",
                type="primary",
                help="A manifest of every match (not just the 50 shown below) -- collection, "
                     "composer, and a source URL or music21 corpus path for each, ready to "
                     "load with pandas and fetch/parse in your own script.",
            )

            with st.expander(f"📦 Bulk downloads for all {len(matches)} match(es) -- MusicXML, PDF, or analysis data"):
                st.caption(
                    "**Which analyses to include** -- leave everything unchecked below for "
                    "plain, unmodified files; check any of the three to get annotated ones "
                    "instead. The same choice applies to the MusicXML and PDF exports below, "
                    "so either version is one click away without re-checking anything."
                )
                bulk_col_cad, bulk_col_pt, bulk_col_hr = st.columns(3)
                bulk_cadences = bulk_col_cad.checkbox("Cadences", value=True, key="browse_bulk_cadences")
                bulk_ptypes = bulk_col_pt.checkbox("Points of imitation", key="browse_bulk_ptypes")
                bulk_hr = bulk_col_hr.checkbox("Homorhythm", key="browse_bulk_hr")
                bulk_annotated = bulk_cadences or bulk_ptypes or bulk_hr

                st.markdown("**MusicXML**")
                if len(matches) > BULK_ZIP_MAX_MATCHES:
                    st.caption(
                        f"Works for up to {BULK_ZIP_MAX_MATCHES} matches at once (this search has "
                        f"{len(matches)}) -- narrow the search to enable it, or use the CSV above "
                        "for the full list."
                    )
                elif st.button(
                    f"📦 Build a ZIP of all {len(matches)} {'annotated ' if bulk_annotated else ''}score(s) (MusicXML)",
                    key="browse_zip_build", type="primary",
                ):
                    progress_bar = st.progress(0.0)
                    status = st.empty()

                    def _update_zip_progress(i, total, label):
                        progress_bar.progress(i / total)
                        status.caption(f"{'Analyzing' if bulk_annotated else 'Fetching'} {i + 1}/{total}: {label}")

                    with st.spinner(_random_fetch_message() if not bulk_annotated else _random_loading_message()):
                        zip_bytes, failed = _bulk_zip_bytes(
                            matches, bulk_cadences, bulk_ptypes, bulk_hr,
                            progress_callback=_update_zip_progress,
                        )
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
                        type="primary",
                    )

                st.markdown("**PDF**")
                if len(matches) > BULK_PDF_ZIP_MAX_MATCHES:
                    st.caption(
                        f"Works for up to {BULK_PDF_ZIP_MAX_MATCHES} matches at once (this search "
                        f"has {len(matches)}) -- narrow the search to enable it."
                    )
                elif st.button(
                    f"📄 Build a ZIP of all {len(matches)} piece(s) as {'annotated ' if bulk_annotated else 'plain '}PDFs",
                    key="browse_pdf_zip_build", type="primary",
                ):
                    progress_bar = st.progress(0.0)
                    status = st.empty()

                    def _update_pdf_zip_progress(i, total, label):
                        progress_bar.progress(i / total)
                        status.caption(f"Rendering {i + 1}/{total}: {label}")

                    with st.spinner(_random_loading_message()):
                        pdf_zip_bytes, failed = _bulk_pdf_zip_bytes(
                            matches, bulk_cadences, bulk_ptypes, bulk_hr,
                            progress_callback=_update_pdf_zip_progress,
                        )
                    progress_bar.progress(1.0)
                    status.empty()

                    if failed:
                        detail = "; ".join(f"{label} ({reason})" for label, reason in failed[:5])
                        st.warning(
                            f"{len(failed)} of {len(matches)} piece(s) couldn't be included and were "
                            f"skipped: {detail}" + (", ..." if len(failed) > 5 else "")
                        )
                    st.download_button(
                        f"Download ZIP ({len(matches) - len(failed)} PDF(s))",
                        data=pdf_zip_bytes,
                        file_name="browse_results_pdfs.zip",
                        mime="application/zip",
                        key="browse_pdf_zip_download",
                        type="primary",
                    )

                st.markdown("**Analysis data (CSV)**")
                st.caption(
                    "Runs the checked analyses on every match and hands back CRIM's own raw "
                    "columns (CadType/Tone/RelTone for cadences, Presentation_Type/Soggetti/"
                    "Voices for points of imitation, hr_voices for homorhythm) as one CSV per "
                    "analysis, ready for your own stats -- not just a count of what was found. "
                    "Also builds a per-piece density comparison CSV, one row per piece, side by "
                    "side. Unlike MusicXML/PDF above, this one genuinely needs at least one "
                    "analysis checked -- there's no 'plain' version of an analysis-data export."
                )
                if len(matches) > BULK_ANALYSIS_MAX_MATCHES:
                    st.caption(
                        f"Works for up to {BULK_ANALYSIS_MAX_MATCHES} matches at once (this search has "
                        f"{len(matches)}) -- narrow the search to enable it. This cap is provisional: "
                        "unlike the ZIP cap above, real CRIM computation cost per piece hasn't been "
                        "benchmarked live yet."
                    )
                elif not bulk_annotated:
                    st.caption("Check at least one analysis above to enable this export.")
                elif st.button(f"🧮 Build analysis-data CSV(s) for all {len(matches)} piece(s)", key="browse_bulk_analysis_build", type="primary"):
                    progress_bar = st.progress(0.0)
                    status = st.empty()

                    def _update_bulk_analysis_progress(i, total, label):
                        progress_bar.progress(i / total)
                        status.caption(f"Analyzing {i + 1}/{total}: {label}")

                    with st.spinner(_random_loading_message()):
                        csv_by_analysis, failed = _bulk_analysis_csv_bytes(
                            matches, bulk_cadences, bulk_ptypes, bulk_hr,
                            progress_callback=_update_bulk_analysis_progress,
                        )
                    progress_bar.progress(1.0)
                    status.empty()

                    if failed:
                        detail = "; ".join(f"{label} ({reason})" for label, reason in failed[:5])
                        st.warning(
                            f"{len(failed)} of {len(matches)} piece(s) couldn't be included and were "
                            f"skipped: {detail}" + (", ..." if len(failed) > 5 else "")
                        )
                    if not csv_by_analysis:
                        st.info("None of the checked analyses found anything across these pieces.")
                    for analysis_key, (btn_label, file_name) in BULK_ANALYSIS_DOWNLOAD_META.items():
                        if analysis_key in csv_by_analysis:
                            st.download_button(
                                btn_label,
                                data=csv_by_analysis[analysis_key],
                                file_name=file_name,
                                mime="text/csv",
                                key=f"browse_bulk_download_{analysis_key}",
                                type="primary",
                            )

            st.divider()
            st.markdown(
                "**One piece at a time** -- pick a single match below to preview it or run "
                "the full analysis interactively (the bulk options above cover every match "
                "at once)."
            )
            browse_label = st.selectbox("Pick one", [m[0] for m in shown], key="browse_pick")
            _, collection, native_ref = next(m for m in shown if m[0] == browse_label)
            stem = _browse_piece_filename_stem(collection, native_ref)
            render_preview_and_annotate(collection, native_ref, browse_label, stem, key_prefix='browse')

    st.caption(
        "☁️ Composer word cloud across all ~4,300 pieces in this app, sized by piece count "
        "(all 7 collections merged, with known same-composer name variants folded together -- "
        "JRP's inverted \"Lastname, Firstname\" order, plus a short manually verified alias list, "
        "e.g. CRIM's \"Giovanni Pierluigi da Palestrina\" merging into \"Palestrina\". A rarer, "
        "unverified variant can still show up as two words):"
    )
    st.image(_composer_wordcloud_png_bytes(full_index))

with tab_upload:
    st.caption("Accepted formats: MusicXML (.xml/.musicxml) or MEI (.mei).")
    uploaded = st.file_uploader("Score file", type=['xml', 'musicxml', 'mei'])
    include_cadences_upload = st.checkbox("Annotate cadences", value=True, key="cadences_upload")
    include_ptypes_upload = st.checkbox("Mark points of imitation", key="ptypes_upload")
    include_homorhythm_upload = st.checkbox("Mark homorhythmic passages", key="hr_upload")
    # Same reasoning as render_preview_and_annotate's action_label -- see
    # that comment for why this isn't just always "Download".
    action_label_upload = "Analyze" if (include_cadences_upload or include_ptypes_upload or include_homorhythm_upload) else "Download"
    # Same session_state pattern as render_preview_and_annotate -- see its
    # comment for why show_result() can't be rendered directly inside this
    # button's own if-block once it contains a nested button (Build PDF).
    # Keyed by the uploaded file's own name so a different upload doesn't
    # keep showing a stale previous result.
    upload_result_key = f"upload_{uploaded.name}_result" if uploaded is not None else None
    if uploaded is not None and st.button(action_label_upload, key="annotate_upload", type="primary"):
        with st.spinner(_random_loading_message()):
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
            st.session_state.pop(upload_result_key, None)
            st.error(error)
        else:
            st.session_state[upload_result_key] = (
                annotated_score, stats, include_cadences_upload, include_ptypes_upload, include_homorhythm_upload,
            )

    if upload_result_key is not None and upload_result_key in st.session_state:
        stored_score, stored_stats, stored_cad, stored_pt, stored_hr = st.session_state[upload_result_key]
        show_result(
            stored_score, stored_stats, Path(uploaded.name).stem, include_cadences=stored_cad,
            include_ptypes=stored_pt, include_homorhythm=stored_hr,
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
    # Composer is *also* real, structured per-piece data here (p['composer']
    # ['name']) -- unlike genre, composer filtering isn't CRIM-exclusive
    # (JRP/1520s/Tasso/SEILS below all get their own version too, since
    # each of those has clean composer data within its own collection --
    # see _composer_filter_widget), it's genre specifically that's unique
    # to CRIM's own data model.
    # Single-select with an "All ..." default -- same interaction as the
    # music21 tab's own Composer picker, not a multiselect: picking several
    # composers or genres at once just mixed different things into one
    # alphabetized list without actually being useful, and broke consistency
    # with the one picker this app already had.
    filter_col1, filter_col2 = st.columns(2)
    composer_choice = filter_col1.selectbox(
        "Composer", ["All composers"] + sorted({p['composer']['name'] for p in crim_pieces}),
        key="crim_composer_filter",
    )
    genre_choice = filter_col2.selectbox(
        "Genre", ["All genres"] + sorted({p['genre']['name'] for p in crim_pieces}),
        key="crim_genre_filter",
    )
    if composer_choice != "All composers":
        crim_pieces = [p for p in crim_pieces if p['composer']['name'] == composer_choice]
    if genre_choice != "All genres":
        crim_pieces = [p for p in crim_pieces if p['genre']['name'] == genre_choice]

    # label -> full piece dict, so selecting a label gets us straight back to
    # its mei_links entry without a second lookup pass
    crim_options = {
        f"{p['composer']['name']} — {p['full_title']} [{p['genre']['name']}]": p
        for p in crim_pieces
    }
    if not crim_options:
        st.info("No pieces match that filter.")
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
    jrp_pieces = _composer_filter_widget(jrp_pieces, key="jrp_composer_filter")
    if not jrp_pieces:
        st.info("No pieces match that composer filter.")
    else:
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
    p1520_pieces = _composer_filter_widget(p1520_pieces, key="p1520_composer_filter")
    if not p1520_pieces:
        st.info("No pieces match that composer filter.")
    else:
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
    tasso_pieces = _composer_filter_widget(tasso_pieces, key="tasso_composer_filter")
    if not tasso_pieces:
        st.info("No pieces match that composer filter.")
    else:
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
    # Lassus's Geistliche Psalmen has no composer prefix in its own labels at
    # all (see fetch_lassus_psalms_pieces -- single-composer collection, no
    # 'Composer — Title' convention to parse); _composer_filter_widget
    # already skips showing itself when everything resolves to one
    # composer, so this correctly shows nothing for that collection and a
    # real filter for SEILS, with no special-casing needed here.
    small_pieces = _composer_filter_widget(small_pieces, key="small_composer_filter")
    if not small_pieces:
        st.info("No pieces match that composer filter.")
    else:
        small_label = st.selectbox("Piece", sorted(small_pieces.keys()), key="small_piece")
        path = small_pieces[small_label]
        render_preview_and_annotate(collection_key, path, small_label, Path(path).stem)
