"""Write provenance notes alongside calibrated Touchstone exports."""

from pathlib import Path

MEASUREMENT_FILES = ("S_short.s2p", "S_open.s2p", "S_load.s2p", "S_thru.s2p")
REFERENCE_FILES = (
    "S_short_P1.s2p",
    "S_open_P1.s2p",
    "S_load_P1.s2p",
    "S_short_P2.s2p",
    "S_open_P2.s2p",
    "S_load_P2.s2p",
    "S_thru.s2p",
)


def save_calibration_note(
    sparameter_filename: str, measurement_dir: str, reference_dir: str,
    ideal: bool = False,
) -> Path:
    """Save calibration input paths next to a calibrated S-parameter file."""
    if not measurement_dir:
        raise ValueError("Calibration measurement path is unavailable.")
    output = Path(sparameter_filename)
    measured = Path(measurement_dir).resolve()
    reference = Path(reference_dir).resolve()
    lines = [
        "# Calibration sources",
        "",
        f"S-parameter file: `{output.name}`",
        "",
        f"Calibration measurements: `{measured}`",
        "",
        ("Reference standards: ideal SOLT (Short=-1, Open=+1, "
         "Load=0, Thru=1)" if ideal else f"Reference standards: `{reference}`"),
        "",
        "## Calibration files",
        "",
    ]
    lines.extend(f"- `{measured / name}`" for name in MEASUREMENT_FILES)
    if not ideal:
        lines.extend(f"- `{reference / name}`" for name in REFERENCE_FILES)
    note_path = output.with_suffix(".md")
    note_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return note_path
