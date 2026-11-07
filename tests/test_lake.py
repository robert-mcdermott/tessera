from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tessera.core.errors import LakeError
from tessera.lake import Lake, LakeWriter
from tessera.lake.compact import compact_dataset, gc
from tessera.lake.manifest import MANIFEST_NAME, read_manifest
from tessera.lake.schemas import ICITE, PUBMED, TABLES
from tessera.lake.writer import STAGING, content_hash


def article(pmid: str, title: str = "A title", authors: tuple[str, ...] = ("Ng",), **extra):  # type: ignore[no-untyped-def]
    return {
        "pmid": pmid,
        "title": title,
        "pub_date": date(2024, 3, 1),
        "pub_year": 2024,
        "date_precision": "month",
        "publication_types": ["Journal Article"],
        "authors": [
            {"position": i, "family": name, "affiliations": ["Example University"]}
            for i, name in enumerate(authors, 1)
        ],
        "mesh": [{"descriptor_ui": "D006801", "descriptor": "Humans", "major_topic": False}],
        **extra,
    }


def current(lake: Lake, sql: str) -> list[dict]:
    lake.refresh(force=True)
    return lake.rows(sql)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "lake"


def test_empty_lake_has_queryable_views(root: Path) -> None:
    with Lake(root) as lake:
        for name in TABLES:
            assert lake.scalar(f'SELECT count(*) FROM "{name}"') == 0
            assert lake.scalar(f'SELECT count(*) FROM "{name}__all"') == 0


def test_write_and_read_bundle(root: Path) -> None:
    with LakeWriter(root, PUBMED, "run_1") as writer:
        writer.add([article("100", authors=("Ng", "Okafor")), article("200")])
    assert writer.stats.written == {"pubmed_author": 3, "pubmed_mesh": 2, "pubmed_article": 2}
    with Lake(root) as lake:
        authors = lake.rows(
            "SELECT pmid, position, family FROM pubmed_author ORDER BY pmid, position"
        )
        assert authors == [
            {"pmid": "100", "position": 1, "family": "Ng"},
            {"pmid": "100", "position": 2, "family": "Okafor"},
            {"pmid": "200", "position": 1, "family": "Ng"},
        ]
        row = lake.rows("SELECT * FROM pubmed_article WHERE pmid = ?", ["100"])[0]
        assert row["pub_date"] == date(2024, 3, 1)
        assert row["_run_id"] == "run_1"
        assert row["_retrieved_at"].tzinfo is not None


def test_unchanged_records_are_skipped(root: Path) -> None:
    with LakeWriter(root, PUBMED, "run_1") as writer:
        writer.add([article("100"), article("200")])
    with LakeWriter(root, PUBMED, "run_2") as writer:
        accepted = writer.add([article("100"), article("200", title="Corrected title")])
    assert accepted == 1
    assert writer.stats.unchanged == 1
    assert writer.stats.written["pubmed_article"] == 1
    with Lake(root) as lake:
        rows = lake.rows("SELECT pmid, title, _run_id FROM pubmed_article ORDER BY pmid")
        assert rows == [
            {"pmid": "100", "title": "A title", "_run_id": "run_1"},
            {"pmid": "200", "title": "Corrected title", "_run_id": "run_2"},
        ]
        assert lake.scalar("SELECT count(*) FROM pubmed_article__all") == 3


def test_child_rows_follow_the_current_root_version(root: Path) -> None:
    with LakeWriter(root, PUBMED, "run_1") as writer:
        writer.add([article("100", authors=("Ng", "Okafor", "Smith"))])
    with LakeWriter(root, PUBMED, "run_2") as writer:
        writer.add([article("100", authors=("Ng", "Okafor"))])  # an author was removed
    with Lake(root) as lake:
        families = [
            r["family"] for r in lake.rows("SELECT family FROM pubmed_author ORDER BY position")
        ]
        assert families == ["Ng", "Okafor"]
        assert lake.scalar("SELECT count(*) FROM pubmed_author__all") == 5


def test_a_change_only_in_children_creates_a_new_version(root: Path) -> None:
    with LakeWriter(root, PUBMED, "run_1") as writer:
        writer.add([article("100", authors=("Ng",))])
    with LakeWriter(root, PUBMED, "run_2") as writer:
        assert writer.add([article("100", authors=("Ng", "Okafor"))]) == 1


def test_duplicates_within_a_run_keep_the_latest(root: Path) -> None:
    with LakeWriter(root, PUBMED, "run_1") as writer:
        writer.add([article("100", title="first")])
        writer.add([article("100", title="second")])
        writer.add([article("100", title="second")])  # identical to the previous: skipped
    with Lake(root) as lake:
        assert lake.rows("SELECT title FROM pubmed_article") == [{"title": "second"}]
        assert lake.scalar("SELECT count(*) FROM pubmed_article__all") == 2


def test_explicit_timestamps_are_kept_monotonic(root: Path) -> None:
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    with LakeWriter(root, ICITE, "run_1") as writer:
        writer.add([{"pmid": "1", "citation_count": 1}], retrieved_at=stamp)
        writer.add([{"pmid": "1", "citation_count": 2}], retrieved_at=stamp)
    with Lake(root) as lake:
        assert lake.rows("SELECT citation_count FROM icite_metrics") == [{"citation_count": 2}]


def test_abort_leaves_nothing_visible(root: Path) -> None:
    with pytest.raises(RuntimeError), LakeWriter(root, PUBMED, "run_1") as writer:
        writer.add([article("100")])
        writer._flush()  # force staged files to exist
        raise RuntimeError("connector failed")
    with Lake(root) as lake:
        assert lake.scalar("SELECT count(*) FROM pubmed_article__all") == 0
    assert not (root / "pubmed_article" / STAGING / "run_1").exists()


def test_crash_after_child_publish_keeps_previous_state(root: Path) -> None:
    """Simulate a crash between publishing child manifests and the root manifest."""
    with LakeWriter(root, PUBMED, "run_1") as writer:
        writer.add([article("100", authors=("Ng",))])
    root_manifest = root / "pubmed_article" / MANIFEST_NAME
    before = root_manifest.read_bytes()
    with LakeWriter(root, PUBMED, "run_2") as writer:
        writer.add([article("100", title="new", authors=("Ng", "Okafor"))])
    root_manifest.write_bytes(before)  # the root swap never happened
    with Lake(root) as lake:
        assert lake.rows("SELECT title FROM pubmed_article") == [{"title": "A title"}]
        assert lake.rows("SELECT family FROM pubmed_author") == [{"family": "Ng"}]


def test_unknown_fields_are_rejected(root: Path) -> None:
    writer = LakeWriter(root, PUBMED, "run_1")
    with pytest.raises(LakeError, match="unknown fields"):
        writer.add([{**article("100"), "surprise": 1}])
    with pytest.raises(LakeError, match="pubmed_author: unknown fields"):
        writer.add([article("100", authors=()) | {"authors": [{"position": 1, "nickname": "x"}]}])
    with pytest.raises(LakeError, match="missing key"):
        writer.add([{"title": "no pmid"}])
    writer.abort()


def test_type_mismatch_is_a_lake_error(root: Path) -> None:
    writer = LakeWriter(root, PUBMED, "run_1")
    writer.add([article("100", pub_year="two thousand")])
    with pytest.raises(LakeError, match="do not match the table schema"):
        writer.commit()


def test_writer_cannot_be_reused(root: Path) -> None:
    writer = LakeWriter(root, ICITE, "run_1")
    writer.add([{"pmid": "1"}])
    writer.commit()
    with pytest.raises(LakeError):
        writer.add([{"pmid": "2"}])


def test_content_hash_ignores_key_order_and_bookkeeping() -> None:
    a = {"pmid": "1", "title": "x", "_run_id": "a"}
    b = {"title": "x", "pmid": "1", "_run_id": "b"}
    assert content_hash(a) == content_hash(b)
    assert content_hash(a) != content_hash({"pmid": "1", "title": "y"})


def test_compaction_preserves_current_rows(root: Path) -> None:
    for run, title in (("run_1", "v1"), ("run_2", "v2"), ("run_3", "v3")):
        with LakeWriter(root, PUBMED, run) as writer:
            writer.add([article("100", title=title, authors=("Ng", "Okafor")), article(f"{run}-x")])
    with Lake(root) as lake:
        before_articles = lake.rows("SELECT pmid, title FROM pubmed_article ORDER BY pmid")
        before_authors = lake.rows(
            "SELECT pmid, position FROM pubmed_author ORDER BY pmid, position"
        )
    report = compact_dataset(root, PUBMED)
    assert report["tables"]["pubmed_article"]["files_after"] == 1
    assert report["tables"]["pubmed_article"]["rows_after"] == 4
    with Lake(root) as lake:
        assert lake.rows("SELECT pmid, title FROM pubmed_article ORDER BY pmid") == before_articles
        assert (
            lake.rows("SELECT pmid, position FROM pubmed_author ORDER BY pmid, position")
            == before_authors
        )
        assert lake.scalar("SELECT count(*) FROM pubmed_article__all") == 4
    assert compact_dataset(root, PUBMED)["skipped"] == "already compact"


def test_writes_after_compaction_still_win(root: Path) -> None:
    with LakeWriter(root, PUBMED, "run_1") as writer:
        writer.add([article("100", title="old")])
    with LakeWriter(root, PUBMED, "run_2") as writer:
        writer.add([article("200")])
    compact_dataset(root, PUBMED)
    with LakeWriter(root, PUBMED, "run_3") as writer:
        writer.add([article("100", title="new")])
    with Lake(root) as lake:
        assert lake.rows("SELECT title FROM pubmed_article WHERE pmid = '100'") == [
            {"title": "new"}
        ]


def test_readers_with_old_views_keep_working_until_gc(root: Path) -> None:
    with LakeWriter(root, ICITE, "run_1") as writer:
        writer.add([{"pmid": "1", "citation_count": 1}])
    with LakeWriter(root, ICITE, "run_2") as writer:
        writer.add([{"pmid": "1", "citation_count": 2}])
    stale = Lake(root, refresh_interval=3600)
    compact_dataset(root, ICITE)
    assert stale.rows("SELECT citation_count FROM icite_metrics") == [{"citation_count": 2}]
    assert gc(root, retention=timedelta(days=7)) == {"files": 0, "staging_folders": 0}
    result = gc(root, retention=timedelta(seconds=-1))
    assert result["files"] == 2
    manifest = read_manifest(root, "icite_metrics", 1)
    assert manifest.superseded == []
    stale.close()
    with Lake(root) as lake:
        assert lake.rows("SELECT citation_count FROM icite_metrics") == [{"citation_count": 2}]


def test_manifest_is_valid_json_with_generations(root: Path) -> None:
    for run in ("run_1", "run_2"):
        with LakeWriter(root, ICITE, run) as writer:
            writer.add([{"pmid": run}])
    manifest = json.loads((root / "icite_metrics" / MANIFEST_NAME).read_text())
    assert manifest["generation"] == 2
    assert [f["run_id"] for f in manifest["files"]] == ["run_1", "run_2"]


def test_stats(root: Path) -> None:
    with LakeWriter(root, PUBMED, "run_1") as writer:
        writer.add([article("100"), article("200")])
    with Lake(root) as lake:
        stats = {s["table"]: s for s in lake.stats()}
    assert stats["pubmed_article"]["rows_current"] == 2
    assert stats["pubmed_article"]["files"] == 1
    assert stats["ctgov_study"]["rows_current"] == 0


def test_lake_never_downloads_extensions(root: Path) -> None:
    with Lake(root) as lake:
        settings = dict(
            lake.cursor()
            .execute(
                "SELECT name, value FROM duckdb_settings() "
                "WHERE name IN ('autoinstall_known_extensions', 'autoload_known_extensions')"
            )
            .fetchall()
        )
    assert settings == {
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
    }


def test_publish_makes_data_visible_mid_run(root: Path) -> None:
    writer = LakeWriter(root, PUBMED, "run_1")
    writer.add([article("100")])
    writer.publish()
    with Lake(root) as lake:
        assert lake.scalar("SELECT count(*) FROM pubmed_article") == 1
    writer.add([article("200"), article("100")])  # 100 is unchanged within the run
    stats = writer.commit()
    assert stats.written == {"pubmed_author": 2, "pubmed_mesh": 2, "pubmed_article": 2}
    assert stats.unchanged == 1
    manifest = read_manifest(root, "pubmed_article", 1)
    assert manifest.generation == 2 and len(manifest.files) == 2
    with Lake(root) as lake:
        assert lake.scalar("SELECT count(*) FROM pubmed_article") == 2
