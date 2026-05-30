"""Shared pytest fixtures for the Brain test suite.

Most tests run fully offline — the llm() seam is monkeypatched per-test.
Only the golden set (marker: golden) hits the real model.
"""
