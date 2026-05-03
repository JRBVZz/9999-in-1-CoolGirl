#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from builder import build

if __name__ == "__main__":
    raise SystemExit(0 if build() else 1)