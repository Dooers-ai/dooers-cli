"""Deprecated: use `dooers.protocol` instead."""

import warnings

warnings.warn(
    "dooers_protocol is deprecated; import from dooers.protocol instead",
    DeprecationWarning,
    stacklevel=2,
)
from dooers.protocol import *  # noqa: E402,F403
