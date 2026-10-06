"""Plain-text CLI output in tests: a terminal that sets FORCE_COLOR makes rich
write color codes into captured output, and substring checks then fail."""

import os

os.environ.pop("FORCE_COLOR", None)
os.environ["NO_COLOR"] = "1"
