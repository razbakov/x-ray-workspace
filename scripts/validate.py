#!/usr/bin/env python3
"""Check a registry against schema.json and the rules a schema cannot express.

  validate.py <path-to-registry.json> [--usage <scan.py output>] [--schema <schema.json>]

Structure — fields, types, enums — comes from schema.json, so the shapes are declared once and
this file does not restate them. Everything below `semantic()` is what JSON Schema cannot reach:
rules that span entries (does this id exist?), rules about the content of a string (does this
regex compile? does this probe write?), and rules that compare the registry against the outside
world (does this usage_keys prefix match anything a transcript actually recorded?).

Errors block, warnings are worth a look. Uses the `jsonschema` package when it is installed, and
otherwise a small validator for the subset of keywords schema.json uses — the repo depends on
nothing, and a validator you cannot run is not a validator.
"""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Repo layout puts the schema at the root, one level above scripts/; a workspace that vendors
# just these two files keeps them side by side. Accept both rather than making callers care.
DEFAULT_SCHEMA = next((p for p in (os.path.join(os.path.dirname(HERE), "schema.json"),
                                   os.path.join(HERE, "schema.json")) if os.path.exists(p)),
                      os.path.join(os.path.dirname(HERE), "schema.json"))

# Built-ins are not services, and are deliberately uncarded.
BUILTIN = {"Bash", "Read", "Write", "Edit", "Glob", "Grep", "ToolSearch", "WebFetch", "WebSearch",
           "Artifact", "Agent", "Task", "Skill", "TodoWrite", "NotebookEdit", "AskUserQuestion",
           "SendUserFile", "TaskOutput", "ScheduleWakeup", "Monitor"}
# Words that have no business in a check that is supposed to only read.
WRITES = ("deploy ", "rm -", "delete ", "send ", "git push", "launchctl load",
          "launchctl unload", "kickstart", " > ", ">>")


# --------------------------------------------------------------------------- structure
def mini_validate(data, schema, root=None, path="$"):
    """The subset of JSON Schema that schema.json uses: $ref (local), type, enum, const,
    required, properties, additionalProperties: false, items, minItems, minLength, pattern.

    Deliberately small. If schema.json grows a keyword this does not know, it is silently not
    enforced in the fallback, so `selftest` below checks that a known-bad registry is rejected.
    """
    root = root if root is not None else schema
    out = []
    if "$ref" in schema:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return mini_validate(data, node, root, path)

    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        ok = any((ty == "object" and isinstance(data, dict))
                 or (ty == "array" and isinstance(data, list))
                 or (ty == "string" and isinstance(data, str))
                 or (ty == "boolean" and isinstance(data, bool))
                 or (ty == "null" and data is None)
                 or (ty == "number" and isinstance(data, (int, float)) and not isinstance(data, bool))
                 or (ty == "integer" and isinstance(data, int) and not isinstance(data, bool))
                 for ty in types)
        if not ok:
            return ["%s: should be %s, got %s" % (path, " or ".join(types), type(data).__name__)]

    if "enum" in schema and data not in schema["enum"]:
        out.append("%s: %r is not one of %s" % (path, data, schema["enum"]))
    if "const" in schema and data != schema["const"]:
        out.append("%s: should be %r" % (path, schema["const"]))

    if isinstance(data, str):
        if "minLength" in schema and len(data) < schema["minLength"]:
            out.append("%s: must not be empty" % path)
        if "pattern" in schema and not re.search(schema["pattern"], data):
            out.append("%s: %r does not match %s" % (path, data, schema["pattern"]))

    if isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            out.append("%s: needs at least %d item(s)" % (path, schema["minItems"]))
        if "items" in schema:
            for i, item in enumerate(data):
                out += mini_validate(item, schema["items"], root, "%s[%d]" % (path, i))

    if isinstance(data, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in data:
                out.append("%s: missing %s" % (path, key))
        if schema.get("additionalProperties") is False:
            for key in data:
                if key not in props:
                    out.append("%s: unknown field %r" % (path, key))
        for key, sub in props.items():
            if key in data:
                out += mini_validate(data[key], sub, root, "%s.%s" % (path, key))
    return out


def structure(reg, schema):
    """Returns (errors, engine-name)."""
    try:
        import jsonschema
    except ImportError:
        return mini_validate(reg, schema), "built-in (install jsonschema for the full checker)"
    v = jsonschema.Draft202012Validator(schema)
    errs = ["$%s: %s" % ("".join("[%r]" % p if isinstance(p, int) else ".%s" % p
                                 for p in e.absolute_path), e.message)
            for e in sorted(v.iter_errors(reg), key=lambda e: list(e.absolute_path))]
    return errs, "jsonschema"


# --------------------------------------------------------------------------- semantics
def semantic(reg, usage):
    """Everything schema.json cannot express."""
    errors, warnings = [], []
    tools = reg.get("tools", [])
    ids = [t.get("id") for t in tools]
    known = set(ids)

    for i in set(ids):
        if ids.count(i) > 1:
            errors.append("duplicate id: %s" % i)

    for t in tools:
        tid = t.get("id", "<no id>")
        for d in t.get("depends_on", []):
            if d not in known:
                errors.append("%s: depends_on %r is not an id in this file" % (tid, d))
            if d == tid:
                errors.append("%s: depends on itself" % tid)
        for a in t.get("alternatives", []):
            if isinstance(a, dict) and a.get("ref") and a["ref"] not in known:
                errors.append("%s: alternative ref %r is not an id in this file" % (tid, a["ref"]))
        if t.get("probe_ok"):
            try:
                re.compile(t["probe_ok"])
            except re.error as e:
                errors.append("%s: probe_ok is not a valid regex (%s)" % (tid, e))
            if not t.get("probe"):
                errors.append("%s: probe_ok without a probe to match it against" % tid)
        if not (t.get("fix") or "").strip():
            errors.append("%s: fix is empty" % tid)
        for word in WRITES:
            if word in (t.get("probe") or "").lower():
                warnings.append("%s: probe contains %r — probes must be read-only" % (tid, word))
        if not t.get("alternatives"):
            warnings.append("%s: no alternatives — single point of failure, or unfilled?" % tid)

    # If A falls back to B, B usually names A. Only within one category: a CLI falling back to a
    # web UI is one-way on purpose, and warning about those drowns the real finding.
    cat = {t.get("id"): t.get("category") for t in tools}
    refs = {t.get("id"): {a.get("ref") for a in t.get("alternatives", [])
                          if isinstance(a, dict) and a.get("ref")} for t in tools}
    for a, rs in refs.items():
        for b in rs:
            if b in refs and a not in refs[b] and cat.get(a) == cat.get(b):
                warnings.append("%s falls back to %s, but %s does not point back" % (a, b, b))

    if usage:
        seen = list(usage.get("tools", {}))
        for t in tools:
            keys = t.get("usage_keys") or []
            if keys and not any(s == k or s.startswith(k) for k in keys for s in seen):
                warnings.append("%s: no usage_keys match any transcript id — typo, or genuinely "
                                "unused?" % t.get("id"))
        carded = {k for t in tools for k in (t.get("usage_keys") or [])}
        orphan = [s for s in seen
                  if not any(s == k or s.startswith(k) for k in carded)
                  and usage["tools"][s]["calls"] >= 5
                  and s not in BUILTIN and not s.startswith(("agent:", "skill:"))]
        if orphan:
            top = sorted(orphan, key=lambda s: -usage["tools"][s]["calls"])[:8]
            warnings.append("%d used tools are in no entry: %s" % (len(orphan), ", ".join(top)))
    return errors, warnings


def load_usage(argv, registry_path):
    if "--usage" in argv:
        where = argv[argv.index("--usage") + 1]
        try:
            return json.load(open(where))
        except Exception as e:
            # A bad --usage file costs you two warnings, not the whole run.
            print("warn  --usage %s is not readable scan.py output (%s); "
                  "skipping the coverage checks" % (where, e))
            return None
    scan = os.path.join(HERE, "scan.py")
    if os.path.exists(scan):
        try:
            return json.loads(subprocess.run([sys.executable, scan], capture_output=True,
                                             text=True, timeout=300).stdout)
        except Exception:
            return None
    return None


def selftest(schema):
    """The fallback validator is only worth having if it actually rejects things."""
    good = {"meta": {"owner": "x", "connected_verified": "2026-01-01"},
            "tools": [{"id": "a", "name": "A", "category": "cli", "access": "x",
                       "connected_now": True, "owner_agent": "o", "also_used_by": [],
                       "usage_keys": [], "depends_on": [], "alternatives": [], "fix": "f",
                       "docs": "", "notes": "", "probe": None}]}
    cases = [("baseline", good, 0)]
    for label, mutate in [
        ("missing field", lambda r: r["tools"][0].pop("fix")),
        ("bad category", lambda r: r["tools"][0].update(category="nope")),
        ("bad id", lambda r: r["tools"][0].update(id="Not An Id")),
        ("wrong type", lambda r: r["tools"][0].update(connected_now="yes")),
        ("unknown field", lambda r: r["tools"][0].update(colour="red")),
        ("empty name", lambda r: r["tools"][0].update(name="")),
        ("bad date", lambda r: r["meta"].update(connected_verified="March")),
        ("no tools", lambda r: r.update(tools=[])),
        ("bad alternative", lambda r: r["tools"][0].update(alternatives=[{"kind": "cli"}])),
    ]:
        bad = json.loads(json.dumps(good))
        mutate(bad)
        cases.append((label, bad, 1))
    failed = 0
    for label, doc, want_errors in cases:
        got = len(mini_validate(doc, schema))
        ok = (got == 0) if want_errors == 0 else (got > 0)
        print("%-18s %s" % (label, "ok" if ok else "NOT CAUGHT"))
        failed += 0 if ok else 1
    print("\nselftest: %d/%d" % (len(cases) - failed, len(cases)))
    return 1 if failed else 0


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit("usage: validate.py <registry.json> [--usage <usage.json>] "
                 "[--schema <schema.json>]\n       validate.py selftest")
    schema_path = argv[argv.index("--schema") + 1] if "--schema" in argv else DEFAULT_SCHEMA
    schema = json.load(open(schema_path))
    if argv[0] == "selftest":
        sys.exit(selftest(schema))

    path = argv[0]
    try:
        reg = json.load(open(path))
    except Exception as e:
        sys.exit("registry does not parse: %s" % e)

    errors, engine = structure(reg, schema)
    if errors:   # structure first: semantic checks assume the shapes are right
        for e in errors:
            print("ERROR " + e)
        print("\nschema (%s): %d structural error(s). Fix these first." % (engine, len(errors)))
        sys.exit(1)

    serrors, warnings = semantic(reg, load_usage(argv, path))
    for w in warnings:
        print("warn  " + w)
    for e in serrors:
        print("ERROR " + e)
    print("\n%d tools, %d errors, %d warnings (schema: %s)"
          % (len(reg["tools"]), len(serrors), len(warnings), engine))
    sys.exit(1 if serrors else 0)


if __name__ == "__main__":
    main()
