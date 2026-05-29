"""Callosum -- The bridge between minds. No API key required."""

import os

os.environ["ANONYMIZED_TELEMETRY"] = "False"

from .cli import main
from .version import __version__

__all__ = ["main", "__version__"]
