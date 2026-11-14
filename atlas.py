"""ATLAS emitter: parses the indented ALA structure and emits its packet."""

import json
from dataclasses import dataclass, field

SRC = """ALA =
VRV
   P3
     M
      Bird
       WLYOME
         char triangle logo
                      Loop sling 3I @LAS
Feed node review live stream string
Return packet
"""


@dataclass
class Node:
    text: str
    indent: int
    children: list = field(default_factory=list)


def parse(src: str) -> list[Node]:
    roots: list[Node] = []
    stack: list[tuple[int, Node]] = []
    for raw in src.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        node = Node(raw.strip(), indent)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        else:
            roots.append(node)
        stack.append((indent, node))
    return roots


def leaves(node: Node) -> list[str]:
    if not node.children:
        return [node.text]
    out = []
    for c in node.children:
        out.extend(leaves(c))
    return out


def render_stream(payload: Node, binding: str) -> str:
    labels = [binding.rstrip(" =")]
    node = payload
    while True:
        labels.append(node.text)
        if len(node.children) != 1:
            break
        node = node.children[0]
    if len(node.children) > 1:
        labels.extend(leaves(node))
    return "/".join(labels)


def node_review(stream: str) -> dict:
    checks = {
        "nonempty": bool(stream),
        "has_handle": "@" in stream,
        "has_logo_descriptor": "logo" in stream,
    }
    return {
        "verdict": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "stream_length": len(stream),
    }


GLOW_GAIN = 16.0      # pushes raw depth signal to the top of the range
CORE_DENSITY = 0.85   # asymptote keeps glow < 1.0 -> max output, no core burn


def tree_depth(node: Node) -> int:
    return 1 + max((tree_depth(c) for c in node.children), default=0)


def tone_map(raw: float) -> float:
    """Asymptotic curve: approaches 1.0 but never reaches it."""
    x = raw * GLOW_GAIN
    return x / (x + CORE_DENSITY)


def py_lights(node: Node, max_depth: int, depth: int = 0) -> list[dict]:
    """Glow-depth readout for every node; deepest nodes burn brightest."""
    out = [{
        "node": node.text,
        "depth": depth,
        "glow": round(tone_map((depth + 1) / max_depth), 4),
    }]
    for c in node.children:
        out.extend(py_lights(c, max_depth, depth + 1))
    return out


def pocket_dimension(forest: list[Node], payload: Node) -> dict:
    """Seals the matter surrounding the payload into a bounded pocket."""
    matter = [n.text for n in forest if n is not payload]
    return {
        "pocket": "sealed",
        "coupled_matter": matter,
        "payload_root": payload.text,
    }


def run(src: str) -> dict:
    forest = parse(src)
    binding_node, payload = forest[0], forest[1]
    stages = forest[2:]

    stream = render_stream(payload, binding_node.text)
    review = node_review(stream)
    lights = py_lights(payload, tree_depth(payload))
    pocket = pocket_dimension(forest, payload)

    return {
        "binding": binding_node.text.rstrip(" ="),
        "payload_root": payload.text,
        "stream": stream,
        "review": review,
        "packet": {
            "stream": stream,
            "review": review,
            "stages": [s.text for s in stages],
            "py_lights": lights,
            "glow_depth_max": max(l["glow"] for l in lights),
            "core_burn": False,
            "pocket_dimension": pocket,
        },
    }


def test_parse():
    forest = parse(SRC)
    assert len(forest) == 4, f"expected 4 roots, got {len(forest)}"
    assert forest[0].text == "ALA ="
    # walk the spine
    node = forest[1]
    spine = [node.text]
    while node.children:
        assert len(node.children) == 1
        node = node.children[0]
        spine.append(node.text)
    assert spine == [
        "VRV", "P3", "M", "Bird", "WLYOME",
        "char triangle logo", "Loop sling 3I @LAS",
    ], spine


def test_emit():
    result = run(SRC)
    assert result["binding"] == "ALA"
    assert result["review"]["verdict"] == "pass"
    assert "@LAS" in result["packet"]["stream"]
    assert result["packet"]["stages"] == [
        "Feed node review live stream string",
        "Return packet",
    ]
    lights = result["packet"]["py_lights"]
    assert result["packet"]["core_burn"] is False
    assert result["packet"]["glow_depth_max"] < 1.0
    assert all(l["glow"] < 1.0 for l in lights)
    assert lights[-1]["glow"] == max(l["glow"] for l in lights)
    pocket = result["packet"]["pocket_dimension"]
    assert pocket["pocket"] == "sealed"
    assert "ALA =" in pocket["coupled_matter"]


if __name__ == "__main__":
    test_parse()
    test_emit()
    print(json.dumps(run(SRC), indent=2))
