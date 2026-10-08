"""Fixed reference vocabulary for ingest reasons (review item: no raw keys in refs)."""

OBJECTS = ["result\\.json", "status\\.json", "heartbeat\\.json", "submission\\.json", "job\\.json", "bundle", "handoff"]
ARTIFACT = "(log|keyframe|contact|preview|final)(-[0-9]{1,6})?"


def property_names(schemas) -> list:
    names = set()

    def walk(node):
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                names.update(props)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for s in schemas:
        walk(s)
    return sorted(names)


def ref_pattern(names) -> str:
    seg = "|".join(names + ["[0-9]{1,2}"])
    return "^(" + "|".join(OBJECTS) + "|" + ARTIFACT + ")(#(/(" + seg + "))*)?$"
