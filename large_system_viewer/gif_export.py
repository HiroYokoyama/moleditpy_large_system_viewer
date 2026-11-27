"""GIF encoding for the trajectory export, kept free of Qt so it can be tested alone.

The options and the encoding match the GIF export of ORCA Result Analyzer: frames
per second, a transparent background, and High Quality (an adaptive palette per
frame). Frames are converted as they are captured, so a long export holds palette
images rather than full screenshots.
"""

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:  # pragma: no cover - Pillow comes with PyVista
    Image = None
    HAS_PIL = False


def convert_frame(img, transparent, use_hq):
    """One captured screenshot, converted for the GIF."""
    if use_hq:
        if transparent:
            alpha = img.convert("RGBA").split()[3]
            frame = img.convert("RGB").convert(
                "P", palette=Image.Palette.ADAPTIVE, colors=255
            )
            mask = Image.eval(alpha, lambda a: 255 if a <= 128 else 0)
            frame.paste(255, mask)
            frame.info["transparency"] = 255
            return frame
        return img.convert("RGB").convert(
            "P", palette=Image.Palette.ADAPTIVE, colors=256
        )
    return img.convert("RGBA") if transparent else img.convert("RGB")


def save_gif(frames, path, fps):
    """Write converted frames as a looping animated GIF."""
    if not frames:
        raise ValueError("no frames to save")
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / max(1, int(fps))),
        loop=0,
        disposal=2,
    )


def export_indices(n_frames, step):
    """Frames written to the GIF: every step-th frame, from the first."""
    return list(range(0, max(0, int(n_frames)), max(1, int(step))))
