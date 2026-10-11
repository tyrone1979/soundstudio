# -*- coding: utf-8 -*-
"""端到端测试：用户原始四段提示词 → DeepSeek 翻译器 → 确定性内核。
产物注册为新聊天会话（不动当前对话），输出到 scores/chats/<newid>.*。
"""
import json
import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(sys.path[0])

USER_PROMPT = """【结构要求】
全曲分为四个部分，连续演奏：
1. 前奏（约 4 小节）
- 调性：降 D 大调或 f 小调
- 织体：分解和弦或琶音铺底，右手引入一个短小的动机
- 情绪：朦胧、安静、像黄昏时分的回忆
- 力度：pp 到 p
2. 冲突段落（约 8 小节）
- 调性开始游离，向关系小调或远关系调偏移
- 织体变厚，左手出现半音下行低音
- 旋律动机被拉宽或碎片化，出现减七和弦、那不勒斯和弦等紧张音响
- 力度：mf 到 f，速度略微加快
- 情绪：不安、挣扎、内心拉扯
3. 高潮（约 6 小节）
- 调性回归主调，旋律以八度或和弦加厚再现
- 织体宽广，左手大跨度琶音或震音
- 力度：ff，可标注 poco rit. 后接 a tempo
- 情绪：宣泄、炽热、全曲情感顶点
4. 平缓收束（约 6 小节）
- 力度逐步回落：f → mp → p → pp
- 织体简化，回到前奏的琶音音型
- 结尾用主和弦长音或上行琶音消逝
- 情绪：释然、余韵、像黄昏沉入夜色
【记谱要求】
- 拍号：4/4，可在冲突段插入 3/4 或 2/4 制造不稳定感
- 调号：明确写出
- 速度标记：Andante 起始，高潮处可标 Agitato，收束处标 Rit. e dim.
- 力度记号、连音线、踏板记号尽量完整
- 右手旋律声部与左手伴奏声部分别用 voice 1 和 voice 2 标注"""


def main():
    from llm_compose import ComposeSession, compose_pipeline
    session = ComposeSession()
    score, plan, summary = compose_pipeline(session, USER_PROMPT)
    spec = session.score_spec

    from score_studio import (m21_to_model, export_musicxml, export_midi,
                              export_wav, render_midi_to_audio, SAMPLE_RATE,
                              export_analysis_report, synthesize_software)
    from llm_compose import spec_to_music21, validate_score_spec

    issues = validate_score_spec(spec)
    print('validate:', json.dumps(issues, ensure_ascii=False))
    if issues:
        print('!! 有校验问题，中止（不产出文件）')
        return 1

    m21 = spec_to_music21(spec)
    model = m21_to_model(m21, spec.get('title') or '黄昏的回忆')

    chat_id = uuid.uuid4().hex[:10]
    base = os.path.join('scores', 'chats', chat_id)
    xml_path, mid_path = base + '.xml', base + '.mid'
    wav_path, rep_path = base + '.wav', base + '_report.md'

    export_musicxml(m21, xml_path)
    export_midi(m21, mid_path)
    audio = render_midi_to_audio(mid_path)
    if audio is None:
        audio = synthesize_software(model, SAMPLE_RATE)
    export_wav(audio, SAMPLE_RATE, path=wav_path)
    export_analysis_report(model, rep_path)

    now = datetime.now().isoformat(timespec='seconds')
    data = {
        'id': chat_id,
        'title': spec.get('title') or '黄昏的回忆（内核测试）',
        'created_at': now,
        'updated_at': now,
        'messages': [
            {'role': 'user', 'text': USER_PROMPT, 'ts': now},
            {'role': 'assistant', 'text': summary, 'ts': now,
             'skills': session.last_skill_ids},
        ],
        'compose': {
            'composition_id': session.composition_id,
            'revision': session.revision,
            'prefs': session.prefs,
            'plan': session.plan,
            'score_spec': spec,
            'last_skill_ids': session.last_skill_ids,
            'last_advice': None,
        },
    }
    with open(base + '.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 注册进索引（插到最前，但不改变 active_id —— 不劫持用户当前对话）
    idx_path = os.path.join('scores', 'chats', 'index.json')
    with open(idx_path, encoding='utf-8') as f:
        index = json.load(f)
    index.setdefault('sessions', []).insert(0, {
        'id': chat_id, 'title': data['title'], 'updated_at': now,
    })
    with open(idx_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print('chat_id:', chat_id)
    print('title:', data['title'])
    print('summary:', summary[:400])
    return 0


if __name__ == '__main__':
    sys.exit(main())
