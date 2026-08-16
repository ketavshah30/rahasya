"""Interactive CIA correlation-web renderer."""

from __future__ import annotations

import html
import json
from typing import Iterable, Optional

from pyvis.network import Network

from rahasya.core.models import EntityType


COLOR_MAP = {
    EntityType.PERSON.value: "#b777ff",
    EntityType.EMAIL.value: "#00e5ff",
    EntityType.PHONE.value: "#41ff96",
    EntityType.USERNAME.value: "#ffd166",
    EntityType.SOCIAL_PROFILE.value: "#ff5ca8",
    EntityType.BREACH_RECORD.value: "#ff4d4d",
    EntityType.DARK_WEB_MENTION.value: "#c21f4a",
    EntityType.URL.value: "#758cff",
    EntityType.PHOTO.value: "#24d6c8",
    EntityType.LOCATION.value: "#73ff5c",
    EntityType.COMPANY.value: "#ff9f43",
    EntityType.TIMELINE_EVENT.value: "#b6ff00",
}
CLUSTER_COLORS = ["#00ff88", "#00e5ff", "#ffca3a", "#ff5ca8", "#a78bfa", "#fb7185"]


def build_pyvis_graph(
    entities,
    relationships,
    *,
    clusters: Optional[dict[str, str]] = None,
    physics: str = "Force-Directed",
    highlight_path: Optional[Iterable[str]] = None,
):
    """Build graph HTML with node click details and cluster boundary rings."""
    clusters = clusters or {}
    path = list(highlight_path or [])
    path_nodes = set(path)
    path_edges = {tuple(sorted((path[i], path[i + 1]))) for i in range(max(0, len(path) - 1))}
    net = Network(height="760px", width="100%", bgcolor="#06100c", font_color="#d8ffe9", directed=True)

    if physics == "Hierarchical (by depth)":
        net.set_options(json.dumps({
            "layout": {"hierarchical": {"enabled": True, "direction": "LR", "sortMethod": "directed", "levelSeparation": 180}},
            "physics": {"hierarchicalRepulsion": {"nodeDistance": 150}},
            "interaction": {"hover": True, "navigationButtons": True},
        }))
    elif physics == "Cluster-focused":
        net.force_atlas_2based(gravity=-45, central_gravity=0.02, spring_length=90, spring_strength=0.12)
    else:
        net.barnes_hut(gravity=-9000, central_gravity=0.25, spring_length=150)

    cluster_indexes = {name: index for index, name in enumerate(sorted(set(clusters.values())))}
    by_id = {entity.id: entity for entity in entities}
    connections = {entity.id: [] for entity in entities}
    for relationship in relationships:
        source = by_id.get(relationship.source_id)
        target = by_id.get(relationship.target_id)
        if source and target:
            connections[source.id].append(
                f"{relationship.relationship_type.value} → {target.value} ({relationship.confidence:.0%}, {relationship.source_module})"
            )
            connections[target.id].append(
                f"{relationship.relationship_type.value} ← {source.value} ({relationship.confidence:.0%}, {relationship.source_module})"
            )
    for entity in entities:
        base_color = COLOR_MAP.get(entity.entity_type.value, "#d8ffe9")
        cluster = clusters.get(entity.id)
        border = CLUSTER_COLORS[cluster_indexes.get(cluster, 0) % len(CLUSTER_COLORS)] if cluster else base_color
        metadata = html.escape(json.dumps(entity.metadata or {}, indent=2, default=str))
        connected_evidence = "<br>".join(html.escape(item) for item in connections.get(entity.id, [])) or "No visible links"
        details = (
            f"<b>{html.escape(str(entity.value))}</b><br>"
            f"Type: {html.escape(entity.entity_type.value)}<br>"
            f"Source: {html.escape(entity.source_module)}<br>"
            f"Confidence: {entity.confidence:.0%}<br>"
            f"Person cluster: {html.escape(cluster or 'unassigned')}<hr>"
            f"<b>Linked breach / mention / identity evidence</b><br>{connected_evidence}"
            f"<hr><b>Raw metadata</b><pre>{metadata}</pre>"
        )
        net.add_node(
            entity.id,
            label=str(entity.value)[:26] + ("…" if len(str(entity.value)) > 26 else ""),
            title=details,
            details=details,
            color={"background": "#f4ff2b" if entity.id in path_nodes else base_color, "border": border},
            borderWidth=6 if cluster else 2,
            size=24 if entity.id in path_nodes else (19 if entity.entity_type == EntityType.PERSON else 14),
            level=entity.depth,
            shape="dot",
        )

    for rel in relationships:
        on_path = tuple(sorted((rel.source_id, rel.target_id))) in path_edges
        net.add_edge(
            rel.source_id,
            rel.target_id,
            label=rel.relationship_type.value,
            title=f"{rel.relationship_type.value} · {rel.confidence:.0%} · {rel.source_module}",
            value=max(1, rel.confidence * 4),
            color="#f4ff2b" if on_path else "rgba(110, 255, 180, 0.45)",
            width=5 if on_path else 1,
            arrows="to",
        )

    generated = net.generate_html()
    panel = """
    <style>
      #intel-panel{position:absolute;right:12px;top:12px;width:300px;max-height:700px;overflow:auto;
      background:rgba(3,14,9,.94);border:1px solid #00ff88;color:#d8ffe9;padding:14px;z-index:99;
      font:12px Consolas,monospace;box-shadow:0 0 18px rgba(0,255,136,.18)}
      #intel-panel pre{white-space:pre-wrap;font-size:10px;color:#9ddfb9} #intel-panel h3{color:#00ff88;margin-top:0}
    </style>
    <div id="intel-panel"><h3>NODE INTELLIGENCE</h3><span>Click a node to inspect sources, confidence, cluster, and metadata.</span></div>
    <script>
      network.on("click", function(params) {
        if (!params.nodes.length) return;
        const node = nodes.get(params.nodes[0]);
        document.getElementById("intel-panel").innerHTML = "<h3>NODE INTELLIGENCE</h3>" + node.details;
      });
    </script>
    """
    return generated.replace("</body>", panel + "</body>")
