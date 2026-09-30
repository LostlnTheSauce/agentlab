"""cPanel "Setup Python App" entry point. Startup file: passenger_wsgi.py, entry point: application."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lab.web import create_app  # noqa: E402

application = create_app()
