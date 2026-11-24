"""Put the vendored splunklib (bin/lib) on sys.path. Imported first by each command."""

import os
import sys

_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
