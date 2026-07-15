"""IOS Knowledge — Knowledge Store (Neo4j-backed)."""
from __future__ import annotations


from typing import Any

# neo4j may not be available in some analysis/dev environments; fall back to Any for typing
try:
    from neo4j import AsyncDriver  # type: ignore
except Exception:  # pragma: no cover - fallback for environments without neo4j
    AsyncDriver = Any

from app.core.logging import get_logger
from app.database.neo4j import (
    delete_entity as neo4j_delete_entity,
    fulltext_search_entities,
    get_entity_by_id,
    get_neighbours as neo4j_get_neighbours,
    run_read_query,
    shortest_path as neo4j_shortest_path,
    upsert_entity as neo4j_upsert_entity,
    upsert_relationship as neo4j_upsert_relationship,
)
from app.knowledge.exceptions import GraphConnectionError
from app.knowledge.interfaces import IEntityStore, IGraphTraverser, IRelationStore
from app.knowledge.types import (
    EntityLabel,
    KnowledgeEntity,
    KnowledgeRelation,
    PathResult,
    RelationType,
    TraversalDirection,
    TraversalResult,
)

logger = get_logger(__name__)


class KnowledgeStore(IEntityStore, IRelationStore, IGraphTraverser):
    """
    Concrete Neo4j-backed implementation of entity, relation, and traversal
    storage interfaces.

    This is the **only** file in `app.knowledge` that imports the Neo4j
    driver or `app.database.neo4j` primitives directly.  Every other
    knowledge component depends only on `IEntityStore` / `IRelationStore` /
    `IGraphTraverser`.
    """

    def __init__(self, driver: AsyncDriver) -> None:
        self._driver = driver

    # ------------------------------------------------------------------
    # IEntityStore
    # ------------------------------------------------------------------

    async def upsert_entity(self, entity: KnowledgeEntity) -> KnowledgeEntity:
        try:
            properties = self._entity_to_properties(entity)
            node = await neo4j_upsert_entity(
                self._driver, entity.id, entity.label.value, properties
            )
            return self._node_to_entity(node, entity.label)
        except Exception as exc:
            raise GraphConnectionError(f"Failed to upsert entity: {exc}") from exc



    async def get(self, entity_id: str) -> KnowledgeEntity | None:
        try:
            node = await get_entity_by_id(self._driver, entity_id)
            if node is None:
                return None
            return self._node_to_entity(node, self._infer_label(node))
        except Exception as exc:
            raise GraphConnectionError(f"Failed to get entity '{entity_id}': {exc}") from exc

    async def get_by_name(self, name: str, label: EntityLabel) -> KnowledgeEntity | None:
        query = f"""
            MATCH (n:{label.value})
            WHERE toLower(n.name) = toLower($name)
            RETURN n
            LIMIT 1
        """
        try:
            results = await run_read_query(self._driver, query, {"name": name})
            if not results:
                return None
            return self._node_to_entity(results[0]["n"], label)
        except Exception as exc:
            raise GraphConnectionError(f"Failed to find entity by name: {exc}") from exc

    async def delete(self, entity_id: str, *, detach: bool = True) -> bool:
        try:
            return await neo4j_delete_entity(self._driver, entity_id, detach=detach)
        except Exception as exc:
            raise GraphConnectionError(f"Failed to delete entity '{entity_id}': {exc}") from exc

    async def list_by_label(
        self, label: EntityLabel, *, limit: int = 100, offset: int = 0
    ) -> list[KnowledgeEntity]:
        query = f"""
            MATCH (n:{label.value})
            RETURN n
            ORDER BY n.updated_at DESC
            SKIP $offset
            LIMIT $limit
        """
        try:
            results = await run_read_query(
                self._driver, query, {"offset": offset, "limit": limit}
            )
            return [self._node_to_entity(r["n"], label) for r in results]
        except Exception as exc:
            raise GraphConnectionError(f"Failed to list entities: {exc}") from exc

    async def fulltext_search(
        self, query: str, *, labels: list[EntityLabel] | None = None, limit: int = 10
    ) -> list[KnowledgeEntity]:
        try:
            label_filter = [label.value for label in labels] if labels else None
            results = await fulltext_search_entities(
                self._driver, query, limit=limit, labels=label_filter
            )
            entities = []
            for r in results:
                node = r["node"]
                label = self._infer_label(node)
                entities.append(self._node_to_entity(node, label))
            return entities
        except Exception as exc:
            raise GraphConnectionError(f"Fulltext search failed: {exc}") from exc

    async def count_entities(self, label: EntityLabel | None = None) -> int:
        if label:
            query = f"MATCH (n:{label.value}) RETURN count(n) AS c"
        else:
            query = "MATCH (n) RETURN count(n) AS c"
        try:
            results = await run_read_query(self._driver, query)
            return int(results[0]["c"]) if results else 0
        except Exception as exc:
            raise GraphConnectionError(f"Failed to count entities: {exc}") from exc

    async def exists(self, entity_id: str) -> bool:
        entity = await self.get(entity_id)
        return entity is not None

    # ------------------------------------------------------------------
    # IRelationStore
    # ------------------------------------------------------------------

    async def upsert_relation(self, relation: KnowledgeRelation) -> bool:
        try:
            properties = {
                "confidence": relation.confidence,
                "weight": relation.weight,
                "source_id": relation.source_id,
                **relation.properties,
            }
            return await neo4j_upsert_relationship(
                self._driver,
                relation.from_id,
                relation.to_id,
                relation.relation_type.value,
                properties,
            )
        except Exception as exc:
            raise GraphConnectionError(f"Failed to upsert relation: {exc}") from exc

    async def upsert(self, item: KnowledgeEntity | KnowledgeRelation):
        """
        Unified dispatcher satisfying both IEntityStore.upsert() and
        IRelationStore.upsert().  Python does not support method overloading
        by parameter type, so this single method inspects the runtime type
        of ``item`` and routes accordingly.
        """
        if isinstance(item, KnowledgeEntity):
            return await self.upsert_entity(item)
        if isinstance(item, KnowledgeRelation):
            return await self.upsert_relation(item)
        raise TypeError(
            f"upsert() expects KnowledgeEntity or KnowledgeRelation, got {type(item).__name__}"
        )

    async def get_relations(
        self,
        entity_id: str,
        *,
        direction: TraversalDirection = TraversalDirection.BOTH,
        relation_types: list[RelationType] | None = None,
    ) -> list[KnowledgeRelation]:
        rel_filter = "|".join(t.value for t in relation_types) if relation_types else ""
        rel_part = f"[r:{rel_filter}]" if rel_filter else "[r]"

        if direction == TraversalDirection.OUT:
            pattern = f"(a {{id: $id}})-{rel_part}->(b)"
        elif direction == TraversalDirection.IN:
            pattern = f"(a {{id: $id}})<-{rel_part}-(b)"
        else:
            pattern = f"(a {{id: $id}})-{rel_part}-(b)"

        query = f"""
            MATCH {pattern}
            RETURN a.id AS from_id, b.id AS to_id, type(r) AS rel_type, r AS props
        """
        try:
            results = await run_read_query(self._driver, query, {"id": entity_id})
            relations = []
            for r in results:
                props = dict(r["props"])
                relations.append(
                    KnowledgeRelation(
                        from_id=r["from_id"],
                        to_id=r["to_id"],
                        relation_type=RelationType(r["rel_type"]),
                        confidence=props.pop("confidence", 1.0),
                        weight=props.pop("weight", 1.0),
                        source_id=props.pop("source_id", None),
                        properties=props,
                    )
                )
            return relations
        except Exception as exc:
            raise GraphConnectionError(f"Failed to get relations: {exc}") from exc

    async def delete_relation(
        self, from_id: str, to_id: str, relation_type: RelationType
    ) -> bool:
        query = f"""
            MATCH (a {{id: $from_id}})-[r:{relation_type.value}]->(b {{id: $to_id}})
            DELETE r
            RETURN count(r) AS deleted
        """
        try:
            results = await run_read_query(
                self._driver, query, {"from_id": from_id, "to_id": to_id}
            )
            return bool(results and results[0].get("deleted", 0) > 0)
        except Exception as exc:
            raise GraphConnectionError(f"Failed to delete relation: {exc}") from exc

    async def delete_all_relations(self, entity_id: str) -> int:
        query = """
            MATCH (n {id: $id})-[r]-()
            DELETE r
            RETURN count(r) AS deleted
        """
        try:
            results = await run_read_query(self._driver, query, {"id": entity_id})
            return int(results[0]["deleted"]) if results else 0
        except Exception as exc:
            raise GraphConnectionError(f"Failed to delete relations: {exc}") from exc

    async def count_relations(self, relation_type: RelationType | None = None) -> int:
        if relation_type:
            query = f"MATCH ()-[r:{relation_type.value}]->() RETURN count(r) AS c"
        else:
            query = "MATCH ()-[r]->() RETURN count(r) AS c"
        try:
            results = await run_read_query(self._driver, query)
            return int(results[0]["c"]) if results else 0
        except Exception as exc:
            raise GraphConnectionError(f"Failed to count relations: {exc}") from exc

    async def count(self, label_or_type: EntityLabel | RelationType | None = None) -> int:
        """
        Unified dispatcher satisfying both IEntityStore.count() and
        IRelationStore.count().  Routes based on the runtime type of the
        filter argument; None defaults to entity count (whole-graph node count).
        """
        if label_or_type is None or isinstance(label_or_type, EntityLabel):
            return await self.count_entities(label_or_type)
        if isinstance(label_or_type, RelationType):
            return await self.count_relations(label_or_type)
        raise TypeError(
            f"count() expects EntityLabel, RelationType, or None, got {type(label_or_type).__name__}"
        )

    # ------------------------------------------------------------------
    # IGraphTraverser
    # ------------------------------------------------------------------

    async def traverse(
        self,
        root_id: str,
        *,
        direction: TraversalDirection = TraversalDirection.BOTH,
        relation_types: list[RelationType] | None = None,
        max_depth: int = 2,
        limit: int = 50,
    ) -> TraversalResult:
        try:
            rel_filter = [t.value for t in relation_types] if relation_types else None
            neighbours = await neo4j_get_neighbours(
                self._driver,
                root_id,
                rel_types=rel_filter,
                direction=direction.value,
                max_depth=max_depth,
                limit=limit,
            )
            nodes = []
            relations = []
            for n in neighbours:
                node = n["neighbour"]
                label = self._infer_label(node)
                nodes.append(self._node_to_entity(node, label))
                relations.append(
                    KnowledgeRelation(
                        from_id=root_id,
                        to_id=node.get("id", ""),
                        relation_type=RelationType(n["rel_type"]) if n.get("rel_type") in RelationType._value2member_map_ else RelationType.RELATED_TO,
                    )
                )
            depth_reached = max((n.get("depth", 0) for n in neighbours), default=0)
            return TraversalResult(
                root_id=root_id, nodes=nodes, relations=relations, depth_reached=depth_reached
            )
        except Exception as exc:
            raise GraphConnectionError(f"Traversal failed: {exc}") from exc

    async def shortest_path(
        self,
        from_id: str,
        to_id: str,
        *,
        max_depth: int = 6,
        relation_types: list[RelationType] | None = None,
    ) -> PathResult:
        try:
            rel_filter = [t.value for t in relation_types] if relation_types else None
            path_nodes = await neo4j_shortest_path(
                self._driver, from_id, to_id, max_depth=max_depth, rel_types=rel_filter
            )
            if path_nodes is None:
                return PathResult(from_id=from_id, to_id=to_id, found=False)
            entities = [
                self._node_to_entity(n, self._infer_label(n)) for n in path_nodes
            ]
            return PathResult(
                from_id=from_id,
                to_id=to_id,
                path_nodes=entities,
                path_length=max(0, len(entities) - 1),
                found=True,
            )
        except Exception as exc:
            raise GraphConnectionError(f"Shortest path failed: {exc}") from exc

    async def get_neighbours(
        self, entity_id: str, *, depth: int = 1, limit: int = 50
    ) -> list[KnowledgeEntity]:
        result = await self.traverse(
            entity_id, direction=TraversalDirection.BOTH, max_depth=depth, limit=limit
        )
        return result.nodes

    # ------------------------------------------------------------------
    # Internal conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _entity_to_properties(entity: KnowledgeEntity) -> dict:
        return {
            "name": entity.name,
            "description": entity.description,
            "tags": entity.tags,
            "confidence": entity.confidence,
            "source_id": entity.source_id,
            "source_type": entity.source_type,
            "version": entity.version,
            "embedding_id": entity.embedding_id,
            **entity.properties,
        }

    @staticmethod
    def _node_to_entity(node: dict, label: EntityLabel) -> KnowledgeEntity:
        node = dict(node)
        return KnowledgeEntity(
            id=node.get("id", ""),
            label=label,
            name=node.get("name", ""),
            description=node.get("description"),
            properties={
                k: v
                for k, v in node.items()
                if k not in {"id", "name", "description", "tags", "confidence",
                             "source_id", "source_type", "version", "embedding_id",
                             "created_at", "updated_at"}
            },
            tags=node.get("tags") or [],
            confidence=node.get("confidence", 1.0),
            source_id=node.get("source_id"),
            source_type=node.get("source_type"),
            version=node.get("version", 1),
            embedding_id=node.get("embedding_id"),
        )

    @staticmethod
    def _infer_label(node: dict) -> EntityLabel:
        """Best-effort label inference from Neo4j node labels metadata."""
        labels = getattr(node, "labels", None)
        if labels:
            for lbl in labels:
                if lbl in EntityLabel._value2member_map_:
                    return EntityLabel(lbl)
        return EntityLabel.ENTITY