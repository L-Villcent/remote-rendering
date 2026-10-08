"""Regenerate result-v1 $defs.ref.pattern from the property names of all v1 schemas.

Run after any schema property change: python3 -I tools/update_ref_pattern.py
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "refimpl"))
from render_protocol.vocab import property_names, ref_pattern  # noqa: E402

paths = {n: ROOT / "schemas" / f"{n}-v1.schema.json" for n in ("job", "status", "result")}
schemas = {n: json.loads(p.read_text(encoding="utf-8")) for n, p in paths.items()}
pattern = ref_pattern(property_names(schemas.values()))
res = schemas["result"]
old = res["$defs"]["ref"]["pattern"]
res["$defs"]["ref"]["pattern"] = pattern
text = paths["result"].read_text(encoding="utf-8")
text = text.replace(json.dumps(old)[1:-1], json.dumps(pattern)[1:-1], 1) if old != pattern else text
assert json.loads(text)["$defs"]["ref"]["pattern"] == pattern
paths["result"].write_text(text, encoding="utf-8", newline="\n")
print("ref vocabulary:", len(property_names(schemas.values())), "names;", "updated" if old != pattern else "unchanged")
