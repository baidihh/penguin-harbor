"""Configure the official NetEase CLI and complete its QR login in a terminal."""

from pathlib import Path
import subprocess
import sys

if __package__:
    from .netease_account import NetEaseAccount
else:
    from netease_account import NetEaseAccount


def main():
    command = NetEaseAccount().command
    if not command:
        runtime = Path(__file__).resolve().parent / '.runtime'
        print('请先安装 Node.js 18 或更新版本，然后安装网易云官方账号工具：', flush=True)
        print(f'npm install --prefix "{runtime / "netease-cli"}" --no-save --ignore-scripts @music163/ncm-cli@0.1.7')
        return 1
    check = subprocess.run([*command, 'config', 'get', 'appId'], capture_output=True, text=True, timeout=15)
    setting = check.stdout.strip()
    configured = (check.returncode == 0 and setting.startswith('appId:')
                  and setting.split(':', 1)[1].strip() not in ('', '(未配置)'))
    if not configured:
        print('请准备网易云音乐开放平台的 App ID 与 Private Key。', flush=True)
        print('https://developer.music.163.com/st/developer/apply/account?type=INDIVIDUAL', flush=True)
        if subprocess.call([*command, 'configure']):
            return 1
    print('请用手机网易云音乐扫一扫，授权企鹅读取日推和红心歌单。', flush=True)
    return subprocess.call([*command, 'login'])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, subprocess.TimeoutExpired):
        sys.exit('无法连接网易云账号工具，请检查 Node.js 和网络。')
