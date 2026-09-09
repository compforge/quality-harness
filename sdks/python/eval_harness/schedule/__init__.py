"""Scheduling: the reconcile engine + per-endpoint rate gates.

The engine drives the Worksheet toward "all cells filled" (缺啥补啥), bounded
only by per-endpoint LLM/API rate and cell dependencies. It is pipelined by
construction: each row's solve→score chain runs concurrently, so the SUT
endpoint and the judge endpoint stay busy at the same time.
"""
