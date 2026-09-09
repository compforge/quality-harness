"""Producers that fill Worksheet cells.

- ``mock`` — dependency-free demo producers (echo solver) for examples / --mock.
- (live) a real Provisioner/Solver (e.g. a live SUT adapter) lives in the
  **consumer** project and is injected into ``run_experiment``; the engine treats it
  identically to the mock. eval_harness ships no live adapter of its own.
"""
