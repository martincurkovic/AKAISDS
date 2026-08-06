# sys.path setup so tests can imports the app's own modules the same way that main.py does

import sys
import os

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))
