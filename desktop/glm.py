"""智谱 GLM 客户端：只负责一次请求，不持有业务状态。"""

import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request


ENDPOINT = 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
# glm-4-flash-250414：免费、约 0.5-6 秒、支持函数调用与代码生成。
# glm-z1-flash 虽然也免费，但思维链会混进正文且不支持函数调用，不适合桌宠。
DEFAULT_MODEL = 'glm-4-flash-250414'
KEY_ENV = 'GLM_API_KEY'
KEY_FILE = Path(__file__).with_name('.glm-key')


def load_key():
    """密钥优先读环境变量，其次读本地文件；两者都没有就当作未启用。"""
    key = os.environ.get(KEY_ENV, '').strip()
    if key:
        return key
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding='utf-8').strip()
    return ''


def save_key(key):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=KEY_FILE.parent,
                                     prefix='.glm-key-', delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(key + '\n')
            output.flush()
            temporary.replace(KEY_FILE)
        finally:
            temporary.unlink(missing_ok=True)


class GlmError(RuntimeError):
    pass


class Glm:
    def __init__(self, key=None, model=DEFAULT_MODEL, timeout=45):
        self.key = (key if key is not None else load_key()).strip()
        self.model = model
        self.timeout = timeout

    @property
    def ready(self):
        return bool(self.key)

    def complete(self, messages, tools=None, temperature=0.8, max_tokens=1200, thinking=False, json_object=False):
        if not self.ready:
            raise GlmError('没有配置 GLM 密钥。')
        payload = {'model': self.model, 'messages': messages,
                   'temperature': max(0.0, min(1.0, float(temperature))), 'max_tokens': max_tokens}
        if json_object:
            payload['response_format'] = {'type': 'json_object'}
        if not thinking:
            # 默认关掉强制推理：开着要 20 秒以上，关掉 1-2 秒，闲聊用不上推理。
            payload['thinking'] = {'type': 'disabled'}
        if tools:
            payload.update({'tools': tools, 'tool_choice': 'auto'})
        request = urllib.request.Request(
            ENDPOINT, data=json.dumps(payload).encode('utf-8'),
            headers={'Authorization': self.key, 'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode('utf-8', errors='replace')[:300]
            raise GlmError(f'GLM 返回 {error.code}: {detail}') from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise GlmError(f'GLM 连不上：{error}') from error
        except json.JSONDecodeError as error:
            raise GlmError('GLM 返回了无法解析的内容。') from error
        try:
            return data['choices'][0]['message']
        except (KeyError, IndexError) as error:
            raise GlmError('GLM 没有返回可用的回复。') from error

    def json_once(self, messages, temperature=0.7, max_tokens=600):
        """要求模型只回 JSON，解析失败时抛出明确错误。"""
        message = self.complete(messages, temperature=temperature, max_tokens=max_tokens, json_object=True)
        text = (message.get('content') or '').strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[-1].rsplit('```', 1)[0]
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise GlmError('GLM 没有返回合法 JSON。') from error
