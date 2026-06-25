"""Make standalone ``tools/`` scripts importable from tests.

``tools/`` is not a package and is intentionally outside the installed package and
pytest's ``testpaths``. Putting it on ``sys.path`` here lets the offline tests import
a runner's pure helpers without turning the runner into shipped library code.
"""

import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
