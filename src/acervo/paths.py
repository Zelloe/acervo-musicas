"""Where the program keeps its own files and the user's data."""

import os
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
RULES = PACKAGE / "data"
STATIC = PACKAGE / "web" / "static"
SOURCE_CHECKOUT = PACKAGE.parents[1]


def default_data_folder() -> Path:
    # A checkout keeps backups and reports beside the code, as it always has.
    if (SOURCE_CHECKOUT / "pyproject.toml").is_file():
        return SOURCE_CHECKOUT
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "acervo"


DATA = default_data_folder()
