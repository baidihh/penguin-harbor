"""Read the locally installed QQ penguin animation sheet."""

from io import BytesIO


FRAME_SIZE = (192, 208)
ANIMATIONS = {
    'idle': (0, 6),
    'running-right': (1, 8),
    'running-left': (2, 8),
    'waving': (3, 4),
    'jumping': (4, 5),
}


def read_frames(asset):
    if not asset.is_file():
        return {}
    from PIL import Image

    width, height = FRAME_SIZE
    with Image.open(asset) as atlas:
        if atlas.size != (width * 8, height * 11):
            raise ValueError('企鹅图集需要使用 1536 × 2288 的透明 WebP 文件。')
        atlas = atlas.convert('RGBA')
        return {
            name: tuple(atlas.crop((column * width, row * height, (column + 1) * width, (row + 1) * height))
                        for column in range(count))
            for name, (row, count) in ANIMATIONS.items()
        }


def png_bytes(frame):
    output = BytesIO()
    frame.save(output, format='PNG')
    return output.getvalue()


def worn_earbuds(ears):
    left = ears.get('left') == 'worn'
    right = ears.get('right') == 'worn'
    return 'both' if left and right else 'left' if left else 'right' if right else ''


def with_earbuds(frame, worn):
    """Fit a small, stemless earbud inside the penguin's silhouette."""
    if not worn:
        return frame
    from PIL import ImageDraw

    alpha = frame.getchannel('A')
    bounds = alpha.getbbox()
    if bounds is None:
        return frame
    _, top, _, bottom = bounds
    y = top + round((bottom - top) * 0.33)
    head = alpha.crop((0, y, frame.width, y + 1)).point(lambda value: 255 if value >= 128 else 0).getbbox()
    if head is None:
        return frame
    decorated = frame.copy()
    draw = ImageDraw.Draw(decorated)
    for side, edge, sign in (('right', head[0], 1), ('left', head[2] - 1, -1)):
        if worn not in (side, 'both'):
            continue
        x = max(7, min(frame.width - 8, edge + sign * 9))
        draw.rounded_rectangle((x - 4, y - 5, x + 4, y + 6), radius=4,
                               fill='#829eab', outline='#354b56')
        draw.rounded_rectangle((x - 2, y - 4, x + 2, y + 3), radius=2, fill='#c4d3d7')
        draw.point((x + sign, y + 3), fill='#55707c')
    return decorated
