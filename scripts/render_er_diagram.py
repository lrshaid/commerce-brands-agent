from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.semantic.model import SemanticModel


def main() -> int:
    model = SemanticModel()
    errors = model.validate()
    if errors:
        raise SystemExit("\n".join(errors))
    lines = ["erDiagram"]
    for name in sorted(model.entities):
        entity = model.entities[name]
        lines.extend(
            [
                f"    {name} {{",
                f"        string {entity['primary_key']} PK",
                "    }",
            ]
        )
    for relationship in model.relationships:
        left = relationship["from"]
        right = relationship["to"]
        label = relationship["name"]
        lines.append(f'    {left} ||--o{{ {right} : "{label}"')
    target = ROOT / "docs" / "entity_graph.mmd"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
