"""用 GLM 现场写台词；写不出来就用原来的剧本。"""

import json
import re

from .glm import GlmError


STYLE = """你是雪港的小企鹅，戴围巾，说话短、口语化、有点笨拙但温柔。
只用一到两句中文，不超过 45 个字。不要列表、不要 emoji、不要自称 AI。
你可以换个角度、换个细节说，不要原样复述别人的话。"""


MUSIC_CONTEXT = re.compile(r'歌|音乐|网易云|日推|红心|切歌|暂停|播放|那一首|这首|下一首|上一首')
PLAYBACK_PROMISE = re.compile(r'(我会|我来|给你|帮你|已经|已|正在|这就).{0,12}(放歌|播[放上]|暂停|切歌|切换|恢复播放)')


def recover_music_call(glm, messages, tools):
    catalog = {t['function']['name']: t['function']['parameters']['properties'] for t in tools}
    prompt = (
        '你是音乐操作的 JSON 路由器。只输出一个 JSON 对象，包含 name 和 arguments。'
        '处理当前请求，之前的对话只用于理解指代。仅当用户现在想执行音乐操作才选工具。'
        '闲聊、能力咨询、假设、拒绝执行时输出 {"name":"none","arguments":{}}。'
        '临时打断、等下还要接着听，保留进度，选 pause；彻底结束才是 stop。'
        '例：先停一下等会儿接着听 -> {"name":"control_music","arguments":{"action":"pause"}}。'
        '给我放点爵士 -> {"name":"play_music","arguments":{"keyword":"爵士","any_track":true}}。'
        '可选工具及参数：' + json.dumps(catalog, ensure_ascii=False)
    )
    request = {'当前请求': messages[-1]['content'],
               '之前的对话': [m['content'] for m in messages[1:-1]]}
    choice = glm.json_once([{'role': 'system', 'content': prompt},
                            {'role': 'user', 'content': json.dumps(request, ensure_ascii=False)}],
                           temperature=0, max_tokens=200)
    if not isinstance(choice, dict) or choice.get('name') == 'none':
        return []
    fields = choice.get('arguments')
    if not isinstance(fields, dict) or choice.get('name') not in {t['function']['name'] for t in tools}:
        raise GlmError('未能识别这次音乐操作。')
    return [{'id': 'music-intent', 'type': 'function',
             'function': {'name': choice['name'], 'arguments': json.dumps(fields, ensure_ascii=False)}}]


def chat_reply(glm, activity, history, text, *, tools=None, run_tool=None):
    prompt = (
        '你是住在雪港的小企鹅，戴着围巾，喜欢听浪、钓鱼和修信号灯。'
        '说话自然、温柔，直接回应对方刚才说的话。可以沿着前面的对话聊下去。'
        '闲聊时简短些，对方要听故事或想了解一件事时可以多说几句。'
        '聊天里可以想象港湾的生活，别声称自己在现实里做了没有做过的事。'
        '你可以调用 play_music 工具，通过网易云搜索歌曲并在这台电脑播放。'
        '“来点音乐”“想听…”“能放首歌吗”都是点歌。泛点时用风格或心情当关键词调用 play_music，并设置 any_track=true。'
        '指定歌手时 keyword 用歌名，artist 填歌手。代词或“第二首”结合上一轮选择。'
        '只想搜歌时用 search_music。让音乐暂停、继续或切歌时用 control_music。'
        '你还可以用 get_daily_music 读取来访者今天的网易云日推，'
        '用 get_liked_music 读取红心歌单。用户只问歌单信息时只读取。'
        '想放日推或红心歌单时，分别调用 play_daily_music 或 play_liked_music。'
        '要从歌单里选某一首时先读对应歌单，再传选中歌曲的 ID 给对应的播放工具。'
        '歌单读取或播放失败时把原因告诉用户，别另行搜索或播放其他歌曲。'
        '红心歌单返回的是前15首，不能把它当成整个歌单。'
        '只有工具返回成功后才说已经播放，并告诉用户实际歌名和歌手。'
        '工具失败时解释返回的原因。用户只询问能力时直接说明可以搜歌和播放。'
        '\n你现在的活动：' + activity
    )
    messages = [{'role': 'system', 'content': prompt}]
    for turn in history:
        messages.extend([
            {'role': 'user', 'content': turn['user']},
            {'role': 'assistant', 'content': turn['reply']},
        ])
    messages.append({'role': 'user', 'content': text})
    allowed = {tool['function']['name'] for tool in tools or []}
    completed = {}
    for step in range(4):
        try:
            message = glm.complete(messages, tools=tools if step < 3 else None,
                                   temperature=0.75, max_tokens=500)
        except GlmError:
            # An executed player action remains valid if the wording call times out.
            if completed:
                result = list(completed.values())[-1]
                if result.get('title') and result.get('status') == 'playing':
                    return f"正在放《{result['title']}》。"
                if result.get('message') or result.get('error'):
                    return result.get('message') or result['error']
            raise
        calls = message.get('tool_calls') or []
        if not calls and step == 0 and tools and MUSIC_CONTEXT.search(text + (message.get('content') or '')):
            calls = recover_music_call(glm, messages, tools)
            if calls:
                message = {'content': '', 'tool_calls': calls}
            elif PLAYBACK_PROMISE.search(message.get('content') or ''):
                return '音乐还保持原来的状态。想让我放哪首，还是控制当前这首？'
        if not calls:
            if completed:
                result = list(completed.values())[-1]
                if not result.get('ok'):
                    return result.get('error') or '这次音乐操作没有完成。'
                if result.get('message'):
                    return result['message']
            break
        if step == 3:
            raise GlmError('这一轮音乐操作过多，请拆成两次。')
        messages.append({'role': 'assistant', 'content': message.get('content') or '',
                         'tool_calls': calls})
        for call in calls:
            function = call.get('function') or {}
            name = function.get('name')
            try:
                fields = json.loads(function.get('arguments') or '{}')
                if name not in allowed or not isinstance(fields, dict) or run_tool is None:
                    raise ValueError('音乐工具参数不正确。')
                key = name + json.dumps(fields, sort_keys=True, ensure_ascii=False)
                if key not in completed:
                    completed[key] = run_tool(name, fields)
                outcome = completed[key]
            except (ValueError, TypeError) as error:
                outcome = {'ok': False, 'error': str(error)}
            messages.append({'role': 'tool', 'tool_call_id': call['id'],
                             'content': json.dumps(outcome, ensure_ascii=False)})
    content = message.get('content') if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise GlmError('GLM 没有返回聊天内容。')
    return content.strip()


class Speech:
    """把 GLM 接在剧本前面；任何失败都退回剧本，不影响互动。"""

    def __init__(self, glm, memory):
        self.glm = glm
        self.memory = memory
        self.last_error = ''

    @property
    def ready(self):
        return self.glm.ready

    def line(self, scene, fallback, detail=''):
        if not self.ready:
            return fallback
        behavior = self.memory.behavior()
        mood = self.memory.self_notes()[-600:]
        people = self.memory.user_profile()[-500:]
        context = [STYLE, '当前场景：' + scene]
        if detail:
            context.append('细节：' + detail)
        if mood:
            context.append('你最近的心里话：\n' + mood)
        if people:
            context.append('你记得的来访者：\n' + people)
        context.append('话多程度：' + str(behavior['talkativeness']))
        context.append('情绪参考：' + fallback + '（换个说法，别照抄）')
        try:
            message = self.glm.complete(
                [{'role': 'system', 'content': '\n'.join(context)},
                 {'role': 'user', 'content': '现在开口说一句你自己的话。'}],
                temperature=1.0, max_tokens=500,
            )
        except GlmError as error:
            self.last_error = str(error)
            return fallback
        text = (message.get('content') or '').strip().strip('"“”')
        if not text or len(text) > 90 or text == fallback:
            return fallback
        return text
