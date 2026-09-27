"""MCP adapter (plan §29; ADR-006; Phase 12): skills extension + stable tools.

Generic client protocol surface — no MCP type ever crosses into the domain or
application layers (ADR-004). Requests are stateless (§29.4): durable state
lives in the registry via `route_run_id`/`bundle_id`, never in MCP session
memory.
"""
