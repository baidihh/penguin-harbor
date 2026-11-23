"""企鹅能写的一小块代码：只影响自己的行为，碰不到进程和网络。"""

import builtins
from pathlib import Path


READABLE = ('十进制数字', '文字', '少量数学')
ALLOWED_BUILTINS = {
    'abs': abs, 'min': min, 'max': max, 'round': round, 'len': len, 'sum': sum,
    'sorted': sorted, 'str': str, 'int': int, 'float': float, 'bool': bool,
    'list': list, 'dict': dict, 'tuple': tuple, 'enumerate': enumerate,
    'range': range, 'zip': zip, 'any': any, 'all': all, 'True': True, 'False': False,
    'None': None,
}
FORBIDDEN = ('import', '__', 'open', 'exec', 'eval', 'compile', 'input', 'globals',
             'locals', 'vars', 'getattr', 'setattr', 'delattr', 'breakpoint', 'memoryview')


class PluginError(ValueError):
    pass


class Plugin:
    """一个纯函数插件：给状态，返回想说的话。没有 IO，没有网络，没有文件。"""

    def __init__(self, path):
        self.path = Path(path)
        self.error = ''
        self.source = ''

    def read(self):
        return self.path.read_text(encoding='utf-8') if self.path.exists() else ''

    def check(self, source):
        source = source.strip()
        if not source:
            raise PluginError('插件是空的。')
        if len(source) > 4000:
            raise PluginError('插件太长了，请控制在 4000 字以内。')
        for word in FORBIDDEN:
            if word in source:
                raise PluginError(f'插件里不能出现 {word}。')
        return source

    def write(self, source):
        source = self.check(source)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(source, encoding='utf-8')
        temporary.replace(self.path)
        self.source = source
        self.error = ''
        return source

    def run(self, state):
        """在受限命名空间里执行，返回字符串或 None。任何异常都只记录不抛出。"""
        source = self.read()
        if not source:
            return None
        namespace = {'__builtins__': ALLOWED_BUILTINS}
        try:
            exec(compile(source, str(self.path), 'exec'), namespace)
            hook = namespace.get('observe')
            if not callable(hook):
                return None
            result = hook(dict(state))
            return str(result)[:200] if result else None
        except Exception as error:
            self.error = f'{type(error).__name__}: {error}'
            return None
