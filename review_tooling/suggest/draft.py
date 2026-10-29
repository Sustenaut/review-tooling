"""Step 3: one Claude call per group of extracts, returning drafted concepts.

The request is re-runnable, not reproducible (spec Section 7.4): the whole
request body is the `prompt` blob, so anyone can send it again. The PDC
list sits in the system prompt behind a cache breakpoint, so every group
after the first reads it from the cache.
"""

from __future__ import annotations

import json

from tools.suggest import MODEL, EFFORT, SuggestError, canonical_json

INSTRUCTIONS = """\
You group extracts from scientific papers into concepts for Sustenaut's \
reference review. An extract is one value a paper printed, with its quote, \
locator, unit and the system it is about. A concept says what several \
extracts measure, so their values can be pooled and compared: "electricity \
per kilogram of lettuce from an LED plant factory" rather than "energy".

Every extract in a group has the same kind (compatible units). Decide which \
of them measure the same thing, and draft one concept per such set. A set \
may be a single extract when nothing else measures the same thing, but do \
not draft a concept whose members measure different things just to place \
every extract: leave an extract out, saying why, when no concept fits it. \
Each extract is in at most one concept. List an extract under a concept with \
`in: false` when a reader might expect it there but it does not belong, and \
say why (another system boundary, a scenario rather than a measurement, \
another crop or stage).

For each concept give:
- `parent`: the code of the most specific class in the PDC list below that \
the concept belongs under. Copy the code exactly. The job assigns the \
concept its own code under that class.
- `name`: a short noun phrase a practitioner would recognise.
- `unit`: the UCUM unit the concept is best stated in, from its members.
- `includes` and `excludes`: short boundary lines, each one thing, that let a \
reviewer decide membership for a value they have not seen yet.
- `reason`: one or two sentences on why these extracts measure one thing.
- `members`: each extract considered, by id, `in` or not, with `why` in one \
sentence.

Extracts marked `cites` repeat a figure from another work; they may still \
belong to a concept, and a reviewer reads the cited work first. Do not draft \
a concept that duplicates one in "Concepts already drafted"; leave its \
extracts out and name that concept in `why`.

The PDC (Extended Publet Decimal Classification) is the UDC Summary, \
UDC Consortium, CC BY-SA 3.0. Its classes, one per line as `code: name`:
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "parent": {"type": "string"},
                    "name": {"type": "string"},
                    "unit": {"type": "string"},
                    "includes": {"type": "array", "items": {"type": "string"}},
                    "excludes": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "members": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}, "in": {"type": "boolean"},
                                           "why": {"type": "string"}},
                            "required": ["id", "in", "why"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["parent", "name", "unit", "includes", "excludes", "reason", "members"],
                "additionalProperties": False,
            },
        },
        "left_out": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "why": {"type": "string"}},
                "required": ["id", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["concepts", "left_out"],
    "additionalProperties": False,
}

SHOWN = ("id", "source", "source_type", "system", "system_class", "quantity", "value", "unit",
         "locator", "quote", "scope", "scenario", "cites")


def request(group: list[dict], pdc: dict[str, str], concepts: list[dict]) -> dict:
    """The request body for one group. Deterministic for the same inputs."""
    classes = "\n".join(f"{code}: {name}" for code, name in pdc.items())
    kind = group[0]
    extracts = [{k: x[k] for k in SHOWN if x.get(k) is not None} for x in group]
    user = (f"Kind: {kind['kind_name']}\n\n"
            f"Concepts already drafted:\n{json.dumps(concepts, ensure_ascii=False, indent=1)}\n\n"
            f"Extracts:\n{json.dumps(extracts, ensure_ascii=False, indent=1)}")
    return {
        "model": MODEL,
        "max_tokens": 16000,
        "output_config": {"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        "system": [{"type": "text", "text": INSTRUCTIONS + classes, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
    }


def prompt_blob(body: dict) -> bytes:
    return canonical_json(body)


def call(body: dict, client=None) -> tuple[dict, str, dict]:
    """Send one request: (the parsed draft, the model that answered, usage)."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic()
    response = client.messages.create(**body)
    if response.stop_reason == "refusal":
        raise SuggestError(f"refused: {getattr(response.stop_details, 'category', None)}")
    if response.stop_reason == "missing":
        raise SuggestError("no draft for this request")
    if response.stop_reason == "max_tokens":
        raise SuggestError("the draft ran past max_tokens")
    text = next(b.text for b in response.content if b.type == "text")
    usage = {k: getattr(response.usage, k, 0) or 0 for k in
             ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
    return json.loads(text), response.model, usage
