"""Table dependency graph: topological ordering and cycle detection."""

from dataclasses import dataclass, field

from app.models.architecture import Architecture


@dataclass
class DependencyGraph:
    """Edges point from a table to the tables it must be created after."""

    edges: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def from_architecture(cls, architecture: Architecture, include_deferred: bool = False) -> "DependencyGraph":
        graph = cls({t.name: set() for t in architecture.tables})
        for table in architecture.tables:
            for fk in table.foreign_keys:
                if fk.deferred and not include_deferred:
                    continue
                if fk.ref_table in graph.edges and fk.ref_table != table.name:
                    graph.edges[table.name].add(fk.ref_table)
        return graph

    def topological_order(self) -> list[str]:
        """Kahn's algorithm with deterministic (alphabetical) tie-breaking.

        Raises ValueError listing the cyclic tables when no order exists.
        """
        remaining = {node: set(deps) for node, deps in self.edges.items()}
        order: list[str] = []
        while remaining:
            ready = sorted(n for n, deps in remaining.items() if not deps)
            if not ready:
                raise ValueError(f"dependency cycle among: {sorted(remaining)}")
            for node in ready:
                order.append(node)
                del remaining[node]
            for deps in remaining.values():
                deps.difference_update(ready)
        return order

    def find_cycles(self) -> list[list[str]]:
        """Strongly connected components with more than one table (Tarjan)."""
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        components: list[list[str]] = []
        counter = 0

        def visit(node: str) -> None:
            nonlocal counter
            index[node] = low[node] = counter
            counter += 1
            stack.append(node)
            on_stack.add(node)
            for dep in sorted(self.edges[node]):
                if dep not in index:
                    visit(dep)
                    low[node] = min(low[node], low[dep])
                elif dep in on_stack:
                    low[node] = min(low[node], index[dep])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    components.append(sorted(component))

        for node in sorted(self.edges):
            if node not in index:
                visit(node)
        return components
