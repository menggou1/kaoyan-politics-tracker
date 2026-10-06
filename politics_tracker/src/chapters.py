"""The five independent politics ledgers."""

from .calculations import ValidationError

CHAPTERS = ("马原", "毛中特", "新思想", "史纲", "思修")
DEFAULT_CHAPTER = CHAPTERS[0]


def validate_chapter(chapter):
    if not isinstance(chapter, str) or chapter not in CHAPTERS:
        raise ValidationError("请选择且只能选择一个章节：" + " / ".join(CHAPTERS))
    return chapter
