"""Web Goal Agent backend — goal engine and extension link built on ai-web-auto.

Importing this package puts the ai-web-auto project root on sys.path so the
platform's core (`ai_web_auto_backend`) can be reused without installing it.
"""

from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.1.0"

PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PACKAGE_DIR.parent
EXTENSION_ROOT = BACKEND_DIR.parent           # @created-extensions/web-goal-agent
PROJECT_ROOT = EXTENSION_ROOT.parents[1]      # ai-web-auto

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
