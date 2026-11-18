"""任务接口、连接说明和执行器共用这一份写作配置。"""
import copy
VERSION='reader-writer-profiles-v4-20261117'
PROFILES={'lesson': {'model': 'gpt-6.1-sol', 'effort': 'high', 'service_tier': 'default', 'max_parallel': None}, 'answer': {'model': 'gpt-6.1-sol', 'effort': 'high', 'service_tier': 'default', 'max_parallel': None}, 'term_repair': {'model': 'gpt-6.1-sol', 'effort': 'low', 'service_tier': 'default', 'max_parallel': None}, 'coordinator': {'model': 'gpt-6.1-sol', 'effort': 'low', 'service_tier': 'default', 'max_parallel': None}}
LABELS={'gpt-6.1-sol':'Sol','gpt-6.1-terra':'Terra','gpt-6-luna':'Luna','deepseek-flash':'DeepSeek Flash','deepseek-v4-pro':'DeepSeek Pro'}
def writer_profile(role='answer'):
    if role not in PROFILES:raise ValueError('没有这个写作角色：'+str(role))
    return {**copy.deepcopy(PROFILES[role]),'role':role,'policy_version':VERSION}
def writer_label(role='answer'):
    p=writer_profile(role);return LABELS.get(p['model'],p['model'])+' '+p['effort']
def public_profiles():
    return {'version':VERSION,**{r:writer_profile(r)for r in PROFILES},'derived_assets':'章节复用 Next STEP-13 术语和 STEP-14 练习；日常问答沿用原生同次写作和保存接口，只补实际缺项，不另加审稿调用','service_rule':'正文与术语内容补齐使用Sol high；名称校正及专用干净调度使用Sol low，均为普通服务，独立组同时启动。现有Codex主对话不被网页切换模型，按实际会话另计读取、启动、等待和汇报；网页不自动调用模型'}
