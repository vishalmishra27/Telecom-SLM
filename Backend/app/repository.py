from __future__ import annotations

from pathlib import Path

from .models import GraphPayload


class DemoRepository:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def _load_graph(self) -> GraphPayload:
        return self.graph()

    def graph(self, scenario_id: str | None = None, limit: int = 180) -> GraphPayload:
        _ = scenario_id, limit
        return GraphPayload(nodes=[], relationships=[], source="demo", total_nodes=0, total_relationships=0)

    def scenarios(self) -> list[dict[str, Any]]:
        return []
