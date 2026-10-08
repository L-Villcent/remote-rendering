"""Reference implementation of protocol-v1 rules (stdlib only).

canonical    strict JSON parsing, canonical JSON, job identity (R1)
bundle       bundle path / tar member rules incl. Windows aliasing (R4)
semantics    cross-field rules XF-* (R2, R4)
statemachine job/attempt/delivery state machine and worker health (R3)

Used by the VPS gates; the Worker may port the rules and must pass the same
vectors in examples/protocol/.
"""
PROTOCOL_VERSION = 1
