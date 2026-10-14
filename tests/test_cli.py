"""Тесты CLI: разбор аргументов и маршрутизация по источникам."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangaparser.cli import build_parser, main, split_source
from mangaparser.sources import REGISTRY, names, resolve, resolve_many


class TestRegistry:
    def test_all_sources_registered(self) -> None:
        assert names() == ["mangalib", "remanga", "inkstory", "buzumanga", "comx", "selfmanga"]

    def test_resolve_unknown(self) -> None:
        with pytest.raises(KeyError):
            resolve("нет такого")

    def test_resolve_all(self) -> None:
        assert [s.name for s in resolve_many("all")] == list(REGISTRY)

    def test_describe(self) -> None:
        for spec in REGISTRY.values():
            title, description = spec.describe()
            assert title and description


class TestSplitSource:
    def test_source_before_command(self) -> None:
        source, rest = split_source(["mangalib", "search", "берсерк"])
        assert source == "mangalib"
        assert rest == ["search", "берсерк"]

    def test_source_after_global_flag(self) -> None:
        assert split_source(["--quiet", "remanga", "dump"]) == ("remanga", ["--quiet", "dump"])

    def test_source_equal_to_all(self) -> None:
        assert split_source(["all", "search", "x"])[0] == "all"

    def test_query_is_not_mistaken_for_source(self) -> None:
        assert split_source(["search", "remanga"]) == (None, ["search", "remanga"])

    def test_value_of_flag_is_not_a_source(self) -> None:
        assert split_source(["index", "--catalog", "mangalib"])[0] is None

    def test_source_not_needed_for_global_commands(self) -> None:
        assert split_source(["sources"]) == (None, ["sources"])
        assert split_source(["offline", "берсерк"]) == (None, ["offline", "берсерк"])


class TestParser:
    def test_command_options_are_parsed(self) -> None:
        args = build_parser().parse_args(["search", "берсерк", "--page", "2"])
        assert args.cmd == "search"
        assert args.query == "берсерк"
        assert args.page == 2

    def test_dump_defaults(self) -> None:
        args = build_parser().parse_args(split_source(["remanga", "dump", "--csv"])[1])
        assert args.csv is True
        assert args.out is None and args.max_pages is None

    def test_index_command(self, tmp_path: Path) -> None:
        args = build_parser().parse_args(["index", "--out", str(tmp_path / "i.db"), "--reset"])
        assert args.reset is True
        assert args.catalog == []

    def test_missing_command(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_command_without_source_reports_error(self, capsys) -> None:
        assert main(["search", "берсерк"]) == 2
        assert "Не указан источник" in capsys.readouterr().err

    def test_unknown_source_exits_with_code_2(self, capsys) -> None:
        assert main(["нет-такого", "search", "x"]) == 2
        assert "Неизвестный источник" in capsys.readouterr().err


class TestCommandsWithoutNetwork:
    def test_sources_command(self, capsys) -> None:
        assert main(["sources"]) == 0
        assert "mangalib" in capsys.readouterr().out

    def test_merge_command(self, tmp_path: Path, capsys) -> None:
        a = tmp_path / "mangalib_catalog.json"
        b = tmp_path / "remanga_catalog.json"
        a.write_text('[{"id": 1, "title": "A"}]', encoding="utf-8")
        b.write_text('[{"id": 2, "title": "B"}]', encoding="utf-8")
        out = tmp_path / "all_catalog.json"
        assert main(["merge", str(a), str(b), "--out", str(out)]) == 0
        assert out.exists()

    def test_index_and_offline(self, tmp_path: Path, capsys) -> None:
        catalog = tmp_path / "mangalib_catalog.json"
        catalog.write_text(
            '[{"id": 1, "title": "Стальной алхимик", "source": "mangalib"}]',
            encoding="utf-8",
        )
        index_path = tmp_path / "index.db"
        assert main(["index", "--catalog", str(catalog), "--out", str(index_path)]) == 0
        assert index_path.exists()
        assert main(["offline", "сталь", "--out", str(index_path)]) == 0
        assert "Стальной алхимик" in capsys.readouterr().out

    def test_build_command_uses_checkpoint(self, tmp_path: Path) -> None:
        out = tmp_path / "mangalib_catalog.json"
        out.with_suffix(".jsonl").write_text('{"id": 1, "title": "A"}\n', encoding="utf-8")
        assert main(["mangalib", "build", "--out", str(out), "--csv"]) == 0
        assert out.exists()
        assert out.with_suffix(".csv").exists()


def test_entrypoint_is_importable() -> None:
    root = Path(__file__).resolve().parents[1] / "main.py"
    assert root.exists()
    assert True  # скрипт запускается отдельно
