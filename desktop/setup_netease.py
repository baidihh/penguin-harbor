"""把裸 base64 私钥转成 ncm-cli 需要的 PEM，并写入配置。

用法：python setup_netease.py <privateKey文件或裸base64字符串>
ncm-cli 的坑：它直接存裸 base64 时 Node 解不出来，报
"RSA SHA256 签名失败: DECODER routines::unsupported"，
必须存带 PEM 头的格式。
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

BEGIN = '-----BEGIN PRIVATE KEY-----'
END = '-----END PRIVATE KEY-----'


def to_pem(text):
    body = re.sub(r'-----(BEGIN|END) PRIVATE KEY-----', '', text)
    body = re.sub(r'\s+', '', body)
    if not body:
        raise ValueError('没有读到私钥内容。')
    lines = [body[index:index + 64] for index in range(0, len(body), 64)]
    return '\n'.join([BEGIN, *lines, END]) + '\n'


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    source = Path(sys.argv[1])
    raw = source.read_text(encoding='utf-8') if source.exists() else sys.argv[1]
    pem = to_pem(raw)

    with tempfile.TemporaryDirectory() as folder:
        key_file = Path(folder) / 'private.pem'
        key_file.write_text(pem, encoding='utf-8')
        # 必须传文件路径：多行 PEM 作为命令参数会被拆开，命令行解析器会当成非法选项。
        result = subprocess.run(['ncm-cli', 'config', 'set', 'privateKey', str(key_file)],
                                capture_output=True, text=True, shell=False)
    print(result.stdout.strip() or result.stderr.strip())
    check = subprocess.run(['ncm-cli', 'config', 'get', 'privateKey'],
                           capture_output=True, text=True, shell=False)
    stored = check.stdout.strip()
    print('已写入:', stored)
    return 0 if stored.startswith('privateKey: -----BEG') else 1


if __name__ == '__main__':
    sys.exit(main())
