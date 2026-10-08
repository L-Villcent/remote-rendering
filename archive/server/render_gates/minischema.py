"""Stdlib JSON Schema (2020-12 subset) validator for the protocol-v1 schemas.

Gates must run with the standard library only (security-boundary 3), so this
implements exactly the keywords used by schemas/*-v1.schema.json, including
unevaluatedProperties.  An unknown keyword raises immediately instead of being
ignored, so a future schema change cannot silently weaken validation.
Agreement with the jsonschema package is checked by differential tests.
Errors are counted, never rendered with instance values.
"""
import json
import re

ANNOTATION_ONLY = {"$schema", "$id", "$comment", "title", "description", "default", "$defs", "format"}
SUPPORTED = ANNOTATION_ONLY | {
    "$ref", "type", "enum", "const", "pattern", "minLength", "maxLength", "minimum", "maximum",
    "items", "minItems", "maxItems", "uniqueItems", "properties", "required", "additionalProperties",
    "propertyNames", "maxProperties", "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
    "unevaluatedProperties",
}


def _key(v):
    """JSON identity: keeps true/1 and false/0 distinct, objects order-insensitive."""
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def _type_ok(inst, t):
    if t == "null":
        return inst is None
    if t == "boolean":
        return isinstance(inst, bool)
    if t == "integer":
        return (isinstance(inst, int) and not isinstance(inst, bool)) or (isinstance(inst, float) and inst.is_integer())
    if t == "number":
        return isinstance(inst, (int, float)) and not isinstance(inst, bool)
    if t == "string":
        return isinstance(inst, str)
    if t == "array":
        return isinstance(inst, list)
    if t == "object":
        return isinstance(inst, dict)
    raise ValueError("unknown type keyword value")


class MiniSchema:
    def __init__(self, schemas):
        self.docs = {s["$id"]: s for s in schemas}
        self._rx = {}

    @classmethod
    def from_dir(cls, schema_dir):
        import pathlib
        return cls([json.loads(p.read_text(encoding="utf-8")) for p in sorted(pathlib.Path(schema_dir).glob("*-v1.schema.json"))])

    def _resolve(self, ref, base):
        uri, _, frag = ref.partition("#")
        uri = uri or base
        node = self.docs[uri]
        for part in [p for p in frag.split("/") if p]:
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node, uri

    def is_valid(self, instance, ref):
        """ref like 'urn:render-protocol:result:v1' or 'urn:...#/$defs/heartbeat'."""
        schema, base = self._resolve(ref, None)
        return self._v(instance, schema, base)[0]

    def _rxc(self, p):
        r = self._rx.get(p)
        if r is None:
            r = self._rx[p] = re.compile(p)
        return r

    def _v(self, inst, s, base):
        """Returns (ok, evaluated_property_names) for the current instance location."""
        if s is True:
            return True, set()
        if s is False:
            return False, set()
        unknown = set(s) - SUPPORTED
        if unknown:
            raise ValueError("unsupported schema keyword")
        ev = set()
        ok = True

        if "$ref" in s:
            sub, sb = self._resolve(s["$ref"], base)
            r, e = self._v(inst, sub, sb)
            ok &= r
            if r:
                ev |= e
        if "type" in s:
            ts = s["type"] if isinstance(s["type"], list) else [s["type"]]
            ok &= any(_type_ok(inst, t) for t in ts)
        if "enum" in s:
            ok &= _key(inst) in {_key(x) for x in s["enum"]}
        if "const" in s:
            ok &= _key(inst) == _key(s["const"])
        if isinstance(inst, str):
            if "pattern" in s and not self._rxc(s["pattern"]).search(inst):
                ok = False
            if "minLength" in s and len(inst) < s["minLength"]:
                ok = False
            if "maxLength" in s and len(inst) > s["maxLength"]:
                ok = False
        if isinstance(inst, (int, float)) and not isinstance(inst, bool):
            if "minimum" in s and inst < s["minimum"]:
                ok = False
            if "maximum" in s and inst > s["maximum"]:
                ok = False
        if isinstance(inst, list):
            if "minItems" in s and len(inst) < s["minItems"]:
                ok = False
            if "maxItems" in s and len(inst) > s["maxItems"]:
                ok = False
            if s.get("uniqueItems") and len({_key(x) for x in inst}) != len(inst):
                ok = False
            if "items" in s:
                for x in inst:
                    ok &= self._v(x, s["items"], base)[0]
        if isinstance(inst, dict):
            if "required" in s and not all(k in inst for k in s["required"]):
                ok = False
            if "maxProperties" in s and len(inst) > s["maxProperties"]:
                ok = False
            if "propertyNames" in s:
                for k in inst:
                    ok &= self._v(k, s["propertyNames"], base)[0]
            props = s.get("properties", {})
            for k, sub in props.items():
                if k in inst:
                    ok &= self._v(inst[k], sub, base)[0]
                    ev.add(k)
            if "additionalProperties" in s:
                for k in inst:
                    if k not in props:
                        ok &= self._v(inst[k], s["additionalProperties"], base)[0]
                        ev.add(k)
        for sub in s.get("allOf", []):
            r, e = self._v(inst, sub, base)
            ok &= r
            if r:
                ev |= e
        if "anyOf" in s:
            res = [self._v(inst, sub, base) for sub in s["anyOf"]]
            ok &= any(r for r, _ in res)
            for r, e in res:
                if r:
                    ev |= e
        if "oneOf" in s:
            res = [self._v(inst, sub, base) for sub in s["oneOf"]]
            good = [e for r, e in res if r]
            ok &= len(good) == 1
            if len(good) == 1:
                ev |= good[0]
        if "not" in s:
            ok &= not self._v(inst, s["not"], base)[0]
        if "if" in s:
            r, e = self._v(inst, s["if"], base)
            if r:
                ev |= e
                if "then" in s:
                    r2, e2 = self._v(inst, s["then"], base)
                    ok &= r2
                    if r2:
                        ev |= e2
            elif "else" in s:
                r2, e2 = self._v(inst, s["else"], base)
                ok &= r2
                if r2:
                    ev |= e2
        if "unevaluatedProperties" in s and isinstance(inst, dict):
            for k in inst:
                if k not in ev:
                    ok &= self._v(inst[k], s["unevaluatedProperties"], base)[0]
                    ev.add(k)
        return bool(ok), (ev if ok else set())
