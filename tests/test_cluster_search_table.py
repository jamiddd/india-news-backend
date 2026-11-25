"""search_clusters() must match against the narrow cluster_search table, not
story_clusters.headline/summary directly.

The trigram indexes live on cluster_search (scripts/add_cluster_search_table.py).
If the query went back to ILIKE on story_clusters it would silently become a
full table scan again, and nothing else would fail.
"""
import asyncio

from sqlalchemy.dialects import postgresql

from app.models import Base
from app.services import cluster_search as cs


class _FakeResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _FakeDB:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult()


def _sql(q: str, **kwargs) -> str:
    db = _FakeDB()
    asyncio.run(cs.search_clusters(db, q, **kwargs))
    assert db.statements, "search_clusters did not execute a query"
    return str(db.statements[0].compile(dialect=postgresql.dialect()))


def _where(sql: str) -> str:
    return sql.split("WHERE", 1)[1]


def test_plain_search_matches_through_the_side_table():
    where = _where(_sql("wildlife protection laws"))
    assert "FROM cluster_search" in where
    assert "cluster_search.headline ILIKE" in where
    assert "cluster_search.summary ILIKE" in where
    # No direct ILIKE on the busy table's own text columns.
    assert "story_clusters.headline ILIKE" not in where
    assert "story_clusters.summary ILIKE" not in where


def test_search_keeps_recency_order_and_limit():
    sql = _sql("trade", limit=20)
    assert "ORDER BY story_clusters.last_updated_at DESC, story_clusters.id DESC" in sql
    assert "LIMIT" in sql


def test_place_search_uses_the_side_table_for_cluster_text():
    where = _where(_sql("Guwahati"))
    assert "cluster_search.headline ~*" in where
    assert "cluster_search.summary ~*" in where
    assert "story_clusters.headline ~*" not in where
    # Article titles are still searched through articles.
    assert "articles.title ~*" in where


def test_side_table_is_not_in_app_metadata():
    """main.py runs Base.metadata.create_all at startup. If cluster_search were
    registered there, deploying the code before the migration would create an
    empty, trigger-less table and search would quietly return nothing."""
    assert "cluster_search" not in Base.metadata.tables
