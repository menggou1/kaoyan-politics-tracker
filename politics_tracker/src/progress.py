"""Question counts and textbook-inspired colors for the overview."""

CHAPTER_TOTALS = {"马原": 396, "毛中特": 115, "新思想": 220, "史纲": 251, "思修": 166}
CHAPTER_COLORS = {"马原": "#b69a68", "毛中特": "#d87980", "新思想": "#b52d36",
                  "史纲": "#728eab", "思修": "#78a18b"}
BANK_TOTAL = sum(CHAPTER_TOTALS.values())


def progress_html(chapters, selected=None):
    names = [selected] if selected else list(CHAPTER_TOTALS)
    total = CHAPTER_TOTALS[selected] if selected else BANK_TOTAL
    segments = []
    legend = []
    for name in names:
        count = CHAPTER_TOTALS[name]
        done = chapters[name]["a_completed"]
        fill = min(max(done, 0), count) / total * 100
        color = CHAPTER_COLORS[name]
        segments.append(f'<div style="width:{fill:.6f}%;height:100%;background:{color}"></div>')
        legend.append(f'<span style="white-space:nowrap"><span style="display:inline-block;width:0.75em;height:0.75em;background:{color};border-radius:2px;margin-right:0.3em"></span>{name} {done}/{count}</span>')
    label = "，".join(f"{name} {chapters[name]['a_completed']}/{CHAPTER_TOTALS[name]}" for name in names)
    return ('<style>@keyframes chapter-progress-grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}'
            '.chapter-progress-fill{animation:chapter-progress-grow 0.8s ease-out both;transform-origin:left}'
            '@media (prefers-reduced-motion:reduce){.chapter-progress-fill{animation:none}}</style>'
            '<div role="img" aria-label="章节一刷进度：' + label + '">'
            '<div style="height:14px;border-radius:7px;overflow:hidden;background:#e4e7eb">'
            '<div class="chapter-progress-fill" style="display:flex;width:100%;height:100%">' + "".join(segments) +
            '</div></div><div style="display:flex;flex-wrap:wrap;gap:0.5em 1em;margin-top:0.5em;font-size:0.85em">' + "".join(legend) + "</div></div>")
