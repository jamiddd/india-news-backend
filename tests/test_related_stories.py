"""
Pure-logic tests for app/services/related_stories.py's grouping pieces
(ported from scripts/experiment_story_edges.py) — actor selection,
sub-clustering, and dedup. No DB/network; find_related_clusters itself
(the DB-touching entry point) is exercised only manually per the design
doc, same as the source experiment script.
"""
from datetime import datetime, timedelta, timezone

from app.services.related_stories import (
    Cluster,
    TopicGroup,
    build_generic_check,
    dedup_sub_clusters,
    select_topic_groups,
    sub_cluster_topic_group,
)

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def make_cluster(id: int, entity_keys, hours_offset: int = 0, sources: int = 1) -> Cluster:
    return Cluster(
        id=id,
        headline=f"cluster {id}",
        first_seen_at=T0 + timedelta(hours=hours_offset),
        last_updated_at=T0 + timedelta(hours=hours_offset),
        distinct_source_count=sources,
        entity_keys=set(entity_keys),
    )


class TestSelectTopicGroups:
    def test_generic_actor_is_excluded(self):
        clusters = [
            make_cluster(1, {"organization:bjp", "person:a"}),
            make_cluster(2, {"organization:bjp", "person:a"}),
        ]
        is_generic = lambda key: key == "organization:bjp"
        groups = select_topic_groups(clusters, is_generic, subsumption_ratio=0.8)
        actors = {g.actor for g in groups}
        assert actors == {"person:a"}

    def test_two_unrelated_actors_form_separate_groups(self):
        clusters = [
            make_cluster(1, {"person:govinda"}),
            make_cluster(2, {"person:govinda"}),
            make_cluster(3, {"person:kareena_kapoor"}),
            make_cluster(4, {"person:kareena_kapoor"}),
        ]
        groups = select_topic_groups(clusters, lambda k: False, subsumption_ratio=0.8)
        actors = {g.actor for g in groups}
        assert actors == {"person:govinda", "person:kareena_kapoor"}

    def test_subsumed_group_is_dropped(self):
        # Every "Sunita Ahuja" story also mentions Govinda; Govinda has
        # broader support, so it wins the shared members and Sunita's group
        # (near-entirely contained in Govinda's) is dropped.
        clusters = [
            make_cluster(1, {"person:govinda"}),
            make_cluster(2, {"person:govinda", "person:sunita_ahuja"}),
            make_cluster(3, {"person:govinda", "person:sunita_ahuja"}),
        ]
        groups = select_topic_groups(clusters, lambda k: False, subsumption_ratio=0.8)
        actors = {g.actor for g in groups}
        assert actors == {"person:govinda"}


class TestSubClusterTopicGroup:
    def test_splits_unrelated_sub_stories_sharing_only_the_actor(self):
        # Govinda's movie news vs. his divorce news: same actor, no other
        # shared entity, must split into two sub-clusters.
        group = TopicGroup(
            actor="person:govinda",
            actor_display="Govinda",
            members=[
                make_cluster(1, {"person:govinda", "org:movie_studio"}),
                make_cluster(2, {"person:govinda", "org:movie_studio"}),
                make_cluster(3, {"person:govinda", "person:sunita_ahuja"}),
                make_cluster(4, {"person:govinda", "person:sunita_ahuja"}),
            ],
        )
        sub_clusters = sub_cluster_topic_group(group, min_shared=1)
        sizes = sorted(len(sc) for sc in sub_clusters)
        assert sizes == [2, 2]

    def test_keeps_related_sub_story_together(self):
        group = TopicGroup(
            actor="person:govinda",
            actor_display="Govinda",
            members=[
                make_cluster(1, {"person:govinda", "org:movie_studio"}),
                make_cluster(2, {"person:govinda", "org:movie_studio"}),
                make_cluster(3, {"person:govinda", "org:movie_studio"}),
            ],
        )
        sub_clusters = sub_cluster_topic_group(group, min_shared=1)
        assert len(sub_clusters) == 1
        assert len(sub_clusters[0]) == 3


class TestDedupSubClusters:
    def test_near_identical_sub_clusters_keep_only_the_largest(self):
        shared_members = [
            make_cluster(1, {"person:dhanush", "person:kareena_kapoor", "org:bollywood"}),
            make_cluster(2, {"person:dhanush", "person:kareena_kapoor", "org:bollywood"}),
        ]
        group_a = TopicGroup(actor="person:dhanush", actor_display="Dhanush", members=shared_members)
        group_b = TopicGroup(actor="person:kareena_kapoor", actor_display="Kareena Kapoor", members=shared_members)
        raw = [(group_a, shared_members), (group_b, shared_members)]
        deduped = dedup_sub_clusters(raw, subsumption_ratio=0.8)
        assert len(deduped) == 1

    def test_disjoint_sub_clusters_both_kept(self):
        members_a = [make_cluster(1, {"person:a"}), make_cluster(2, {"person:a"})]
        members_b = [make_cluster(3, {"person:b"}), make_cluster(4, {"person:b"})]
        group_a = TopicGroup(actor="person:a", actor_display="A", members=members_a)
        group_b = TopicGroup(actor="person:b", actor_display="B", members=members_b)
        raw = [(group_a, members_a), (group_b, members_b)]
        deduped = dedup_sub_clusters(raw, subsumption_ratio=0.8)
        assert len(deduped) == 2


class TestBuildGenericCheck:
    def test_high_baseline_rate_entity_is_generic(self):
        clusters = [make_cluster(1, {"organization:bjp"}), make_cluster(2, {"organization:bjp"})]
        entity_stats = {"organization:bjp": (100.0, 50.0, "BJP")}
        is_generic = build_generic_check(clusters, entity_stats, generic_percentile=0.5, max_df_ratio=0.5)
        assert is_generic("organization:bjp") is True

    def test_entity_without_stats_falls_back_to_doc_frequency(self):
        clusters = [make_cluster(i, {"person:x"}) for i in range(10)]
        is_generic = build_generic_check(clusters, {}, generic_percentile=0.95, max_df_ratio=0.5)
        # "person:x" appears in all 10/10 clusters — well above a 0.5 df ratio.
        assert is_generic("person:x") is True

    def test_rare_entity_without_stats_is_not_generic(self):
        clusters = [make_cluster(1, {"person:x"}), make_cluster(2, {"person:y"})]
        is_generic = build_generic_check(clusters, {}, generic_percentile=0.95, max_df_ratio=0.5)
        assert is_generic("person:x") is False
