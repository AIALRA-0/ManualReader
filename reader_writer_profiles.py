"""任务接口、连接说明和执行器共用这一份写作配置。"""
import copy
VERSION='reader-writer-profiles-v3-20261108'
PROFILES={'lesson': {'model': 'gpt-6.1-sol', 'effort': 'high', 'service_tier': 'default', 'max_parallel': 2}, 'answer': {'model': 'gpt-6.1-sol', 'effort': 'high', 'service_tier': 'default', 'max_parallel': 2}, 'term_repair': {'model': 'gpt-6.1-sol', 'effort': 'low', 'service_tier': 'default', 'max_parallel': 2}, 'coordinator': {'model': 'gpt-6.1-sol', 'effort': 'low', 'service_tier': 'default', 'max_parallel': 2}}
LABELS={'gpt-6.1-sol':'Sol','gpt-6.1-terra':'Terra','gpt-6-luna':'Luna','deepseek-flash':'DeepSeek Flash','deepseek-v4-pro':'DeepSeek Pro'}
def writer_profile(role='answer'):
    if role not in PROFILES:raise ValueError('没有这个写作角色：'+str(role))
    return {**copy.deepcopy(PROFILES[role]),'role':role,'policy_version':VERSION}
def writer_label(role='answer'):
    p=writer_profile(role);return LABELS.get(p['model'],p['model'])+' '+p['effort']
def public_profiles():
    return {'version':VERSION,**{r:writer_profile(r)for r in PROFILES},'derived_assets':'术语与练习由所属讲解或回答的同一位写作者同次生成，不另起写作或审稿调用','service_rule':'正文使用Sol high；名称补齐及专用干净调度使用Sol low，均为普通服务，最多两路并行。现有Codex主对话不被网页切换模型，按实际会话另计读取、启动、等待和汇报；网页不自动调用模型'}
