# -*- coding: utf-8 -*-
"""
Score-to-Skill learning pipeline.

Principle: learn transferable TECHNIQUES from scores, never memorize note sequences.
  Score → Feature Set (measured) → Aggregate (frequency/confidence)
       → Technique Miner (LLM interprets) → Skill files → (later) Validate
"""
from __future__ import annotations

__version__ = '0.1.0'
