"""End-to-end: an unknown workbook in, the plan out. Nothing changed, nothing spent."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from supportkit.cli import main


def _workbook(tmp_path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "log"
    ws.append(["Дата обращения(受理时间)", "Категория(咨询分类)", "Проблема(咨询问题)",
               "Статус(处理进度)", "UID"])
    long_a = "Отсутствует аккомпанемент и ничего не работает уже неделю, помогите пожалуйста разобраться " * 2
    long_b = "Как записывать с аккомпанементом, не нахожу кнопку и настройки записи в интерфейсе приложения " * 2
    for i in range(24):
        ws.append([f"{(i % 27) + 1} марта(3月{(i % 27) + 1}日)",
                   ["функции", "оплата"][i % 2],
                   [long_a, long_b][i % 2] + str(i),
                   ["Решено", "Не решена"][i % 2],
                   str(650000 + i)])
    path = tmp_path / "export.xlsx"
    wb.save(path)
    return path


class TestProfileCommand:
    def test_the_whole_promise(self, tmp_path: Path, capsys) -> None:
        path = _workbook(tmp_path)
        code = main(["profile", str(path), "--work-dir", str(tmp_path / "work"),
                     "--mapping-out", str(tmp_path / "mapping.json")])
        out = capsys.readouterr().out
        assert code == 0
        assert "PROPOSED MAPPING" in out
        assert "ru_zh_bilingual" in out          # the tournament ran and is cited
        assert "CANNOT ANSWER" in out            # refusals are part of the output
        assert "API key" in out                  # the LLM layer refuses, with the reason
        assert (tmp_path / "mapping.json").exists()

    def test_identifiers_do_not_reach_the_report(self, tmp_path: Path, capsys) -> None:
        main(["profile", str(_workbook(tmp_path)), "--work-dir", str(tmp_path / "work")])
        out = capsys.readouterr().out
        assert "650000" not in out

    def test_missing_file_is_an_error_not_a_traceback(self, tmp_path: Path) -> None:
        assert main(["profile", str(tmp_path / "nope.xlsx")]) == 2

    def test_csv_works_too(self, tmp_path: Path, capsys) -> None:
        src = tmp_path / "export.csv"
        lines = ["date,category,note"]
        for i in range(12):
            lines.append(f"2026-01-{i + 1:02d},billing,note {i}")
        src.write_text("\n".join(lines), encoding="utf-8")
        assert main(["profile", str(src)]) == 0
        assert "iso_like" in capsys.readouterr().out
