"""Install a Yonhee Chan fan-art sprite into the ignored local runtime."""

import argparse
from pathlib import Path
import tempfile
from urllib.request import urlopen

from penguin_sprite import read_frames


UPSTREAM = 'https://raw.githubusercontent.com/YonheeChan/qq-penguin-codex-pet/226448295993300cf0afaf74e3798937299befb2'
THEMES = ('classic-scarf', 'star-egg', 'star-kid', 'star-adult')


def main():
    parser = argparse.ArgumentParser(
        description='Install unofficial Yonhee Chan QQ penguin artwork for personal, non-commercial use.'
    )
    parser.add_argument('--theme', choices=THEMES, default=THEMES[0])
    options = parser.parse_args()
    target = Path(__file__).parent / '.runtime' / 'qq-penguin'
    target.mkdir(parents=True, exist_ok=True)
    files = {
        'LICENSE': 'LICENSE',
        'LEGAL_NOTICE.md': 'LEGAL_NOTICE.md',
        'spritesheet.webp': f'pets/{options.theme}/spritesheet.webp',
    }
    with tempfile.TemporaryDirectory(dir=target) as temporary:
        staging = Path(temporary)
        for name, path in files.items():
            with urlopen(f'{UPSTREAM}/{path}', timeout=25) as response:
                (staging / name).write_bytes(response.read())
        read_frames(staging / 'spritesheet.webp')
        for name in files:
            (staging / name).replace(target / name)
    print(f'Installed {options.theme} in {target}')


if __name__ == '__main__':
    main()
