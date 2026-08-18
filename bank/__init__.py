"""Lloyds Bank core — the bank's own service.

Owns the customer records, the underwriting engine, and the internal
Command Centre dashboard. Runs as a separate process from the ChatGPT-facing
MCP backend, which reaches it over HTTP.
"""
