from src.progress import BANK_TOTAL, CHAPTER_TOTALS, progress_html


def test_progress_segments_are_contiguous_and_use_completed_question_counts():
    chapters = {name: {"a_completed": 0} for name in CHAPTER_TOTALS}
    chapters["马原"]["a_completed"] = 100
    chapters["史纲"]["a_completed"] = 50
    html = progress_html(chapters)
    assert f'width:{100 / BANK_TOTAL * 100:.6f}%' in html
    assert f'width:{50 / BANK_TOTAL * 100:.6f}%' in html
    assert 'class="chapter-progress-fill"' in html
    assert "prefers-reduced-motion:reduce" in html
    assert f'width:{50 / 251 * 100:.6f}%' in progress_html(chapters, "史纲")
