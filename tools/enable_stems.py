"""
Set a CamillaDSP config up for the Music page's stem mixer (audio/stems.py):
inputs 1-2 CarPlay L/R (routing kept as it was), inputs 3-10 the stems
(drums, bass, vocals, other; L/R). The old 5.1 music inputs are dropped.
Capture and mixer become 10 channels, with a source for every stem input
on every output (at the saved placement). A backup (.bak-stems) is written
first.

Usage:  .venv/bin/python tools/enable_stems.py [config.yml]   (default: the 3-way)
"""

import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from audio.stems import FIRST_STEM_INPUT, STEM_INPUTS, apply_to_config, load_state  # noqa: E402

DEFAULT = Path.home() / "CamillaDSP/jeep_3way_lr4.yml"


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    config = yaml.safe_load(path.read_text())

    capture = config["devices"]["capture"]
    mixer = config["mixers"]["route_to_speakers"]
    channels = FIRST_STEM_INPUT + STEM_INPUTS
    backup = path.with_name(path.name + ".bak-stems")
    if not backup.exists():
        shutil.copy2(path, backup)
    capture["channels"] = channels
    mixer["channels"]["in"] = channels
    # Only CarPlay (inputs 1-2) keeps its routing; stems are added below
    for mapping in mixer["mapping"]:
        mapping["sources"] = [s for s in mapping["sources"] if s["channel"] < FIRST_STEM_INPUT]
    mixer["description"] = ("In 1-2 CarPlay L/R, in 3-10 stems (drums, bass, vocals, other; L/R) "
                            "placed on the Music page. Out 1-2 front, 3-4 rear (cargo), 5-6 8in "
                            "mid-bass (2nd row), 7-8 subs.")

    apply_to_config(config, load_state())
    path.write_text(yaml.safe_dump(config, sort_keys=False, width=100))
    print(f"{path.name}: capture {capture['channels']} ch, mixer in {mixer['channels']['in']}")


if __name__ == "__main__":
    main()
