import os
import sys

# Make byos/ modules importable as top-level (state, protocol, registry, ...).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
