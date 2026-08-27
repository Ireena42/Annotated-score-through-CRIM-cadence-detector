"""
RUN IN THE `crim` CONDA ENV ONLY (not `complexp` -- see findings.md #15 for
why crim-intervals and musicntwrk can't share an env).

Re-runs CRIM cadence detection with voice_detail=True, which adds a
'PartMap' column: {CVF letter -> [staff positions performing it]},
1 = highest staff (see crim_intervals main_objs.py, ImportedPiece.cadences
docstring / numberParts). This is the piece needed to know exactly which
note in which voice to annotate -- the plain cadences.csv used elsewhere
in this project (findings.md #15/#16) doesn't have it because voice_detail
defaults to False.

PartMap is a dict per row, which can't go into a CSV cell as-is, so it's
JSON-serialized before saving; annotate_cadences.py (complexp env) reverses
this with json.loads.

Usage (from the crim env):
    python crim_export_cadences.py <path/to/piece.xml> <path/to/out_cadences.csv>
"""
import sys
import json
from pathlib import Path

import crim_intervals as ci


def export_cadences_with_partmap(xml_path, out_csv):
    piece = ci.importScore(str(xml_path))
    df = piece.cadences(voice_detail=True, include_final=True)
    df['PartMap'] = df['PartMap'].apply(json.dumps)
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv)
    return df


if __name__ == '__main__':
    xml_path, out_csv = sys.argv[1], sys.argv[2]
    result = export_cadences_with_partmap(xml_path, out_csv)
    print(f"{len(result)} cadences written to {out_csv}")
