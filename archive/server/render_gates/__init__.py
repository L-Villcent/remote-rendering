"""VPS-side gates for protocol-v1 r5 (simulation-ready, stdlib only).

publish   outbox -> down/jobs, receipts        (identity render-publish)
ingest    up/ -> inbox, acks, views            (identity render-ingest)
netcheck  tailscaled status -> whitelist view  (identity render-netcheck)

Every filesystem location comes from config.Layout, so tests run the same code
against a temporary directory tree.  Nothing here touches the network.
"""
