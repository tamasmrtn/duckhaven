"""Whether a series' own colour can be seen and told apart.

A colour a result column gives is drawn as given, so a team's yellow stays its
yellow. These checks are advice for whoever writes the dashboard: a preview, a
draft, or a chart the assistant drew. A published view never shows them.

The palette's own colours were chosen to pass both checks; only colours a
dashboard brings are checked here.
"""

from collections.abc import Iterable, Mapping

#: The chart surface in each theme: `--bg-surface` in the web app's tokens.
SURFACES = {"light": "#f8fafc", "dark": "#111827"}
#: WCAG 2's floor for a line or a mark to be seen against what is behind it.
MIN_CONTRAST = 3.0
#: OKLab distance, on a 0 to 100 lightness scale, under which two colours read as one.
MIN_DELTA_E = 5.0


def few(names: Iterable[str], noun: str = "") -> str:
    """Up to three names, and how many more: "VER, PER, HAM and 2 more"."""
    names = [n or "(none)" for n in names]
    if len(names) <= 3:
        return " and ".join(filter(None, [", ".join(names[:-1]), names[-1]])) if names else ""
    return f"{', '.join(names[:3])} and {len(names) - 3} more{noun}"


def _linear(hex_colour: str) -> tuple[float, float, float]:
    channels = (int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)


def luminance(hex_colour: str) -> float:
    """WCAG 2 relative luminance of a #rrggbb colour."""
    r, g, b = _linear(hex_colour)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG 2 contrast ratio between two #rrggbb colours, from 1 to 21."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def oklab(hex_colour: str) -> tuple[float, float, float]:
    """A #rrggbb colour in OKLab, scaled by 100."""
    r, g, b = _linear(hex_colour)
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        100 * (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_),
        100 * (1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_),
        100 * (0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_),
    )


def delta_e(a: str, b: str) -> float:
    """How far apart two #rrggbb colours look: OKLab distance, scaled by 100."""
    return sum((x - y) ** 2 for x, y in zip(oklab(a), oklab(b), strict=True)) ** 0.5


def colour_problems(styles: Mapping[str, tuple[str, str]]) -> list[str]:
    """What makes the series in ``styles`` (key to colour and line style) hard to
    see or to tell apart: a colour under 3:1 on either theme's surface, and two
    series in nearly one colour and the same line style. Teammates in one colour,
    one solid and one dotted, are told apart by their line."""
    notes = []
    for theme, surface in SURFACES.items():
        faint = sorted((contrast(colour, surface), key) for key, (colour, _) in styles.items())
        faint = [(ratio, key) for ratio, key in faint if ratio < MIN_CONTRAST]
        if faint:
            named = few(f"{key} ({ratio:.1f}:1)" for ratio, key in faint)
            notes.append(
                f"On the {theme} theme, {named} fall under the {MIN_CONTRAST:.0f}:1 a line "
                "needs to be seen."
            )
    keys = sorted(styles)
    alike = [
        f"{a} and {b}"
        for i, a in enumerate(keys)
        for b in keys[i + 1 :]
        if styles[a][1] == styles[b][1] and delta_e(styles[a][0], styles[b][0]) < MIN_DELTA_E
    ]
    if alike:
        notes.append(
            f"{few(alike, ' pairs')} draw in nearly one colour and the same line style, so "
            "they read as one line."
        )
    return notes
