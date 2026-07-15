"""Performance Gate Agent.

A static, diff-scoped performance reviewer that runs on every code push.
Stage 1: deterministic rule detectors (fast, no LLM) flag candidate hot-spots.
Stage 2: a local LLM (Ollama by default) confirms and explains each candidate,
grounded in a local performance knowledge base.

Everything runs on-prem. No source code, diff, or data ever leaves the network.
"""

__version__ = "1.0.0"
