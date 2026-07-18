"""Render the AgentPack README demo GIF and MP4.

The demo is intentionally scripted instead of screen-recorded so the public
asset is reproducible and does not depend on local shell state.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1120
HEIGHT = 720
FPS = 8

OUTPUT_BASENAME = "agentpack-demo"

FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/Library/Fonts/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
)

LINES: tuple[tuple[str, str], ...] = (
    ("$ agentpack guard --agent codex --repair-stale --refresh-context", "muted"),
    ("[ok] Agent integration current: codex", "ok"),
    ("[ok] Context fresh for current task", "ok"),
    ("", "text"),
    ('$ agentpack route --task "fix billing webhook retry handling"', "muted"),
    ("Task mode: backend_bugfix", "accent"),
    ("Read: src/billing/webhooks.py + tests/test_webhook_retries.py", "text"),
    ("Skill recommendations: review, debugging, test strategy", "accent"),
    ("Warnings: verify idempotency and retry ordering", "warn"),
    ("", "text"),
    ("$ agentpack review --check --dry-run-post", "muted"),
    ("review preflight: payload checked, no live post", "ok"),
    ('$ agentpack learn "retry ordering + idempotency"', "muted"),
    ("lesson saved: .agentpack/agent-lessons.md", "ok"),
    ("", "text"),
    ("$ agentpack memory --timeline --limit 3", "muted"),
    ("kind          confidence  reason", "accent"),
    ("task_start    1.00        map before edits", "text"),
    ("episode       0.95        source hash current", "text"),
    ("memory_edge   0.90        review -> learn", "text"),
    ("stale_paths: 0 | live source stays authority", "ok"),
    ("", "text"),
    ("$ pytest tests/test_webhook_retries.py -q", "muted"),
    ("6 passed in 0.42s", "ok"),
)

PALETTE = {
    "bg": "#0f172a",
    "chrome": "#111827",
    "chrome_border": "#273449",
    "text": "#e5e7eb",
    "muted": "#94a3b8",
    "accent": "#60a5fa",
    "ok": "#34d399",
    "warn": "#fbbf24",
    "title": "#f8fafc",
}


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _visible_lines(frame_index: int) -> int:
    if frame_index < 6:
        return 0
    return min(len(LINES), (frame_index - 6) // 3 + 1)


def _render_frame(frame_index: int, total_frames: int) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), PALETTE["bg"])
    draw = ImageDraw.Draw(image)

    title_font = _font(22)
    mono_font = _font(22)
    small_font = _font(18)

    margin = 34
    terminal_top = 34
    terminal_bottom = HEIGHT - 88
    terminal_width = WIDTH - margin * 2

    draw.rounded_rectangle(
        (margin, terminal_top, margin + terminal_width, terminal_bottom),
        radius=18,
        fill=PALETTE["chrome"],
        outline=PALETTE["chrome_border"],
        width=2,
    )
    draw.rounded_rectangle(
        (margin, terminal_top, margin + terminal_width, terminal_top + 54),
        radius=18,
        fill="#172033",
    )
    draw.rectangle(
        (margin, terminal_top + 36, margin + terminal_width, terminal_top + 54),
        fill="#172033",
    )

    for index, color in enumerate(("#ef4444", "#f59e0b", "#22c55e")):
        x = margin + 25 + index * 24
        draw.ellipse((x - 7, terminal_top + 20, x + 7, terminal_top + 34), fill=color)

    draw.text(
        (margin + 115, terminal_top + 17),
        "AgentPack demo: route -> skills -> review/learn -> memory",
        fill=PALETTE["title"],
        font=title_font,
    )

    y = terminal_top + 84
    visible = _visible_lines(frame_index)
    for text, style in LINES[:visible]:
        draw.text((margin + 36, y), text, fill=PALETTE[style], font=mono_font)
        y += 25 if text else 16

    if visible < len(LINES):
        cursor_x = margin + 36
        if visible:
            previous_text = LINES[visible - 1][0]
            cursor_x += int(draw.textlength(previous_text, font=mono_font)) + 5
        cursor_y = max(terminal_top + 84, y - 25)
        if (frame_index // 4) % 2 == 0:
            draw.rectangle(
                (cursor_x, cursor_y + 2, cursor_x + 13, cursor_y + 25),
                fill=PALETTE["text"],
            )

    progress = min(1.0, frame_index / max(1, total_frames - 1))
    draw.rounded_rectangle((margin, HEIGHT - 40, WIDTH - margin, HEIGHT - 22), radius=9, fill="#1e293b")
    draw.rounded_rectangle(
        (margin, HEIGHT - 40, margin + int(terminal_width * progress), HEIGHT - 22),
        radius=9,
        fill=PALETTE["accent"],
    )
    draw.text(
        (margin, HEIGHT - 62),
        "Local preflight map. Skills, review, learn, and memory stay advisory.",
        fill=PALETTE["muted"],
        font=small_font,
    )

    return image


def render(output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    gif_path = output_dir / f"{OUTPUT_BASENAME}.gif"
    mp4_path = output_dir / f"{OUTPUT_BASENAME}.mp4"

    total_frames = 6 + len(LINES) * 3 + 18
    frames = [_render_frame(index, total_frames) for index in range(total_frames)]
    durations = [1000 // FPS] * len(frames)
    durations[-1] = 1600

    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found; GIF rendered but MP4 could not be written")

    with tempfile.TemporaryDirectory(prefix="agentpack-demo-frames-") as tmpdir:
        frame_dir = Path(tmpdir)
        for index, frame in enumerate(frames):
            frame.save(frame_dir / f"frame_{index:04d}.png")
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-framerate",
                str(FPS),
                "-i",
                str(frame_dir / "frame_%04d.png"),
                "-movflags",
                "+faststart",
                "-pix_fmt",
                "yuv420p",
                str(mp4_path),
            ],
            check=True,
        )

    return gif_path, mp4_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/assets"),
        help="Directory for rendered demo media.",
    )
    args = parser.parse_args()

    gif_path, mp4_path = render(args.output_dir)
    print(f"wrote {gif_path}")
    print(f"wrote {mp4_path}")


if __name__ == "__main__":
    main()
