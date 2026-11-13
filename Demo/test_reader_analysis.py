"""Verify that both reading modes use the same source-scoped analysis and evidence checks."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reader_analysis import analysis_packet, analysis_schema, validate_analysis, validate_writing_evidence
from reader_bridge import feedback_file
from reader_connected import RECAP_FIELDS, prose_line_breaks, validate_connected
from reader_execute import analyze_task, combined_stage_usage
from reader_runs import Runs
from reader_writer_schema import output_schema


def decision(task_id, source, identity, anchor):
    return {"task_id":task_id, "source_ids":[source], "reader_evidence_ids":[],
            "reader_start":"读者刚看到题目。", "task_focus":"解释这一步的动作。",
            "author_approach":"用同一个算例。", "expected_takeaway":"可以独立复算。",
            "prerequisites":[], "verification":[], "helpful_extras":[],
            "gaps":[{"id":identity, "kind":"first_use", "anchor":anchor,
                     "question":"符号怎样影响结果", "handling":"给出实际动作。"}]}


class AnalysisTests(unittest.TestCase):
    def test_bounded_previews_keep_all_source_ids_and_full_writer_input(self):
        sources=[{"id":f"src{i}","original":"甲"*4000} for i in range(12)]
        packet={"mode":"lesson","learning_node":{"id":"node","title":"起点"},"sources":sources}
        original=deepcopy(packet)
        packed=analysis_packet(packet)
        self.assertEqual(packet,original)
        self.assertEqual(len(packed["sources"]),len(sources))
        self.assertLessEqual(sum(len(row["preview"]) for row in packed["sources"]),18000)
        self.assertFalse(packed["sources"][0]["preview_complete"])

    def test_code_excerpt_is_guidance_while_a_state_diagram_is_still_required(self):
        packet=analysis_packet({'questions':[{'id':'q1','text':'这个测试怎样运行'}],
                                'sources':[{'id':'src1','original':'从队首读取。'}]})
        item=decision('q1','src1','first','队首')
        excerpt='从链接中的真实测试代码逐段摘录激励和检查，标出文件位置'
        item['visuals']=[{'id':'excerpt','kind':'original','show':excerpt},
                         {'id':'paths','kind':'diagram','show':'画出读出前后的状态'}]
        validate_analysis(packet,{'mode':'answer','items':[item]})
        self.assertIn(excerpt,item['author_approach'])
        self.assertEqual([row['id'] for row in item['visuals']],['paths'])

    def test_dispatch_runs_analysis_once_without_tools_and_passes_verified_result(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            for mode in ("lesson","answer"):
                target="chapter" if mode=="lesson" else "q1"
                task={"mode":mode,"sources":[{"id":"src1","original":"读第一行。"}],
                      "learning_node":{"id":"chapter","title":"起点"} if mode=="lesson" else None,
                      "questions":[] if mode=="lesson" else [{"id":"q1","text":"怎样读取"}]}
                result={"mode":mode,"chapter_title":"先读第一行" if mode=="lesson" else None,
                        "items":[decision(target,"src1","first","第一行")]}
                if mode=="lesson":
                    result["items"][0]["opening"]={"orientation":"先看清手里有一行文字。","first_action":"把第一行读出来。"}
                scope=folder/mode;scope.mkdir()
                with patch("reader_execute.invoke_writer",return_value=(json.dumps(result),{"usage":{}},"test")) as call:
                    enriched,usage=analyze_task(scope,task,"codex")
                self.assertEqual(call.call_count,1)
                self.assertFalse(call.call_args.kwargs["research"])
                self.assertFalse(call.call_args.kwargs["allow_image_generation"])
                self.assertEqual(call.call_args.args[2],[])
                self.assertEqual(enriched["reader_analysis"],result)
                self.assertEqual(enriched["sources"],task["sources"])
                self.assertEqual(usage["usage"],{})
                self.assertEqual(json.loads((scope/"analysis/result.json").read_text("utf8")),result)
                self.assertIn("### 正文准备怎么讲",(scope/"analysis/plan.md").read_text("utf8"))
                self.assertIn("reader_checks",output_schema(enriched)["required"])

    def test_uses_exact_reader_words_and_checks_questions_against_the_original(self):
        message = "我会加减法。\n我想自己算出总价。"
        with tempfile.TemporaryDirectory() as temp:
            note = Path(temp)/'request.txt'
            note.write_text(message, 'utf8')
            context = feedback_file(note)
        source = '(1) Find the unit price.\n（2）计算总价。'
        packet = analysis_packet({'mode':'lesson','learning_node':{'id':'chapter'},
                                  'sources':[{'id':'src1','original':source,'translation':'只计算总价。'}],
                                  'reference_blocks':[{'id':'ref','original':'(3) Another exercise.'}],**context})
        self.assertEqual(packet['reader_evidence'][0]['value']['text'], message)
        self.assertEqual(packet['sources'][0]['preview'], source)
        self.assertEqual(packet['source_task_steps'], ['(1)','(2)'])
        item = decision('chapter','src1','one','（1） 单价')
        item['opening'] = {'orientation':'先弄清单价和总价之间有什么关系。','first_action':'先算一件商品的单价。'}
        item['gaps'][0]['kind'] = 'task_step'
        item['gaps'].append({'id':'two','kind':'task_step','anchor':'(2)','question':'总价是多少','handling':'按数量计算总价。'})
        result = {'mode':'lesson','items':[item]}
        with self.assertRaisesRegex(ValueError,'这次用户直接说'):
            validate_analysis(packet,result)
        item['reader_evidence_ids'] = ['reader_feedback_latest']
        validate_analysis(packet,result)
        self.assertEqual(item['gaps'][0]['anchor'], '(1)')
        item['gaps'].reverse()
        with self.assertRaisesRegex(ValueError,'按原件顺序'):
            validate_analysis(packet,result)

    def test_requires_independent_opening_items_and_first_action_in_place(self):
        item = decision('chapter','src1','a','(a)')
        item['opening'] = {'orientation':'交代商品和价格。','first_action':'从一件商品算起。'}
        item['gaps'][0]['kind'] = 'task_step'
        item['gaps'].append({'id':'b','kind':'task_step','anchor':'(b)'})
        analysis = {'mode':'lesson','items':[item]}
        intro = '顾客想买三件同样的商品，先弄清单价再看总价。'
        action = '先算一件商品需要付多少，再把它当作计算总价的起点。'
        first = '(a) 算出一件商品的单价。'
        second = '(b) 算出三件商品的总价。'
        value = {'explanation':{'framing':{'start':[{'role':'previous','markdown':intro},
                                                    {'role':'current','markdown':f'- **(a)** {first[4:]}\n- **(b)** {second[4:]}'}]},
                                'parts':[{'id':'p1','markdown':action+'\n\n接下来写出单价和总价。'}]},
                 'reader_checks':[{'addresses':address,'part_id':part,'quote':quote} for address,part,quote in (
                     ('chapter-orientation','opening',intro),('chapter-first-action','p1',action),
                     ('a','opening-question',first),('b','opening-question',second))]}
        validate_writing_evidence(value,analysis)
        moved = deepcopy(value)
        moved['explanation']['framing']['start'][1]['markdown'] = first+'；'+second
        with self.assertRaisesRegex(ValueError,'每小问用一个'):
            validate_writing_evidence(moved,analysis)
        moved = deepcopy(value)
        moved['explanation']['parts'][0]['markdown'] = '这里是其他内容。\n\n'+action
        with self.assertRaisesRegex(ValueError,'正文摘录'):
            validate_writing_evidence(moved,analysis)

    def test_recap_renders_each_sentence_on_its_own_line(self):
        sentences = [f'第 {i} 句完整内容。' for i in range(1,7)]
        bullet = {'part_id':'p1',**dict(zip(RECAP_FIELDS,sentences))}
        draft = {'parts':[{'id':'p1','heading':'单价','premise':'先看标价。','question':'怎么计算总价',
                           'markdown':'先看价格。\n把同一件事接着讲。\n\n再换个问题。'}],
                 'framing':{'start':[{'role':'current','heading':'这一节要弄明白什么','markdown':'先看每件的价格。'}],
                            'map_heading':'阅读流程','end':[{'role':'recap','heading':'把这一节串起来','bullets':[bullet]}]}}
        clean = validate_connected(draft)
        recap = clean['framing']['end'][0]['markdown']
        self.assertEqual(recap.splitlines(), ['- **单价**','',*('  '+s[:-1]+'  ' for s in sentences[:-1]),'  '+sentences[-1][:-1]])
        self.assertIn('先看价格；\n把同一件事接着讲；\n\n再换个问题',clean['parts'][0]['markdown'])
        from reader_sequence import prepare_explanation
        for transition in ('接下来用这组价格算总价。','这组价格能算出多少？'):
            draft['parts'][0]['question']=transition
            prepared=prepare_explanation(draft,preserve_prose=True)
            self.assertEqual(prepared['parts'][0]['question'],transition)
            self.assertEqual(prepared['narrative']['flow'][0]['question'],transition)

    def test_unplanned_ending_uses_the_next_original_after_this_chapter(self):
        from reader_sequence import ending_context
        sources=[{'id':'header','file_id':'text','en':'材料文件的标题'},
                 {'id':'current','file_id':'text','en':'已经讲完的原文'},
                 {'id':'navigation','file_id':'text','en':'页面导航请点击首页','role':'page_furniture'},
                 {'id':'after','file_id':'text','en':'[sid=12；1.2—3.4秒] 接下来装配两件工件'}]
        material={'passages':sources,'learning':{'coverage_plan':{'deferred':['header','navigation','after']}}}
        node={'source_links':[{'file_id':'text','passage_id':'current'}]}
        self.assertEqual(ending_context(material,node,None,[]),{
            'mode':'open','unplanned_source':{'id':'after','excerpt':'接下来装配两件工件'}})
        self.assertEqual(ending_context(material,node,{'id':'child'},[]),{'mode':'next'})

    def test_line_breaks_keep_paragraphs_links_tables_math_and_code(self):
        first = '从手里的记录出发，先找到当前这条记录的两个位置，再分别确认它们指向的内容，沿着连接标出最终实际使用的对象；'
        second = '接着回到原来的列表，沿用同一套画法找出最后一次追加操作修改的是哪个对象，写下来后再运行代码核对。'
        example = first+second
        fence = chr(96)*3
        protected = (
            f'{fence}text\n{example}\n{fence}',
            f'| 标题 | 内容 |\n| --- | --- |\n| 一 | {example} |',
            f'\\[\n\\text{{{example}}}\n\\]',
            f'- {example}\n  - 需要缩进的补充说明',
            f'[参考材料](https://example.test) {example}',
        )
        original = '\n\n'.join([example,*protected])
        formatted = prose_line_breaks(original)
        self.assertEqual(formatted, '\n\n'.join([first+'\n'+second,*protected]))
        self.assertEqual(prose_line_breaks(formatted), formatted)

    def test_rejects_source_not_in_input_and_cross_question_quote(self):
        task=analysis_packet({"questions":[{"id":"q1","text":"第一问"},{"id":"q2","text":"第二问"}],
                              "sources":[{"id":"src1","original":"原文。"}]})
        analysis={"mode":"answer","chapter_title":None,"items":[decision("q1","src1","g1","队首"),
                                                                  decision("q2","src1","g2","队尾")]}
        bad=deepcopy(analysis);bad["items"][0]["source_ids"]=["missing"]
        with self.assertRaisesRegex(ValueError,"未提供"):
            validate_analysis(task,bad)
        validate_analysis(task,analysis)
        q1="队首记录下一次读取的位置。";q2="队尾记录下一次写入的位置。"
        value={"answers":[{"question_id":"q1","parts":[{"id":"p","markdown":q1}]},
                          {"question_id":"q2","parts":[{"id":"p","markdown":q2}]}],
               "reader_checks":[{"addresses":"g1","part_id":"p","quote":q2}]}
        with self.assertRaisesRegex(ValueError,"同一问题"):
            validate_writing_evidence(value,analysis)
        value["reader_checks"]=[{"addresses":"g1","part_id":"p","quote":q1},{"addresses":"g2","part_id":"p","quote":q2}]
        validate_writing_evidence(value,analysis)

    def test_checks_the_visible_text_across_a_bold_label(self):
        item = decision('chapter','src1','part_a','(a)')
        item['gaps'][0]['kind'] = 'task_step'
        analysis = {'mode':'lesson','items':[item]}
        paragraph = '**(a) 朴素法：**从基准树出发，逐个试加候选点。'
        value = {'explanation':{'parts':[{'id':'p1','markdown':paragraph}]},
                 'reader_checks':[{'addresses':'part_a','part_id':'p1','quote':'(a) 朴素法：从基准树出发，逐个试加候选点。'}]}
        validate_writing_evidence(value,analysis)
        value['reader_checks'][0]['quote'] = '(a) 朴素法：**从基准树出发，逐个试加候选点。'
        validate_writing_evidence(value,analysis)
        value['reader_checks'][0]['quote'] = '从基准树出发，逐个试加候选点。'
        with self.assertRaisesRegex(ValueError,'小问题号'):
            validate_writing_evidence(value,analysis)

    def test_first_use_accepts_a_contiguous_explanation_in_the_same_paragraph(self):
        item = decision('q1','src1','edge','<=')
        analysis = {'mode':'answer','items':[item]}
        definition = '`<=` 让寄存器的更新在本次时钟沿之后生效。'
        detail = '先按更新前的地址写入第零槽，再把写地址加一。'
        part = {'id':'p1','markdown':definition + '\n' + detail}
        value = {'answers':[{'question_id':'q1','parts':[part]}],
                 'reader_checks':[{'addresses':'edge','part_id':'p1','quote':detail}]}
        validate_writing_evidence(value,analysis)
        part['markdown'] = definition + '\n\n' + detail
        with self.assertRaisesRegex(ValueError,'同段术语原词'):
            validate_writing_evidence(value,analysis)

    def test_first_use_can_explain_a_labeled_rule_just_below_its_table(self):
        item = decision('q1','src1','rule','保持上一分钟的开关状态')
        analysis = {'mode':'answer','items':[item]}
        text = '这时沿用原来开着或关着的决定，再结算这一分钟的水量。'
        table = '| 条件 | 规则 |\n| --- | --- |\n| 中间区间 | 保持上一分钟的开关状态 |'
        part = {'id':'p1','markdown':table+'\n\n'+text}
        value = {'answers':[{'question_id':'q1','parts':[part]}],
                 'reader_checks':[{'addresses':'rule','part_id':'p1','quote':text}]}
        validate_writing_evidence(value,analysis)
        part['markdown'] = table+'\n\n这里谈另一件事。\n\n'+text
        with self.assertRaisesRegex(ValueError,'同段术语原词'):
            validate_writing_evidence(value,analysis)

    def test_current_answer_can_give_an_example_right_after_the_definition(self):
        from reader_prompts import WRITING_VERSION
        item=decision('q1','src1','retry','退避等待')
        other=decision('q2','src2','different','退避等待')
        analysis={'mode':'answer','items':[item,other]}
        definition='退避等待会让发送方在碰撞后错开发送时间。'
        example='两台机器分别等两个和五个时隙，然后在不同时间重发。'
        part={'id':'p1','markdown':definition+'\n\n'+example}
        other_part={'id':'p1','markdown':definition}
        value={'writing_version':WRITING_VERSION,'answers':[{'question_id':'q1','parts':[part]},
               {'question_id':'q2','parts':[other_part]}],'reader_checks':[
                   {'addresses':'retry','part_id':'p1','quote':example},
                   {'addresses':'different','part_id':'p1','quote':definition}]}
        validate_writing_evidence(value,analysis)
        part['markdown']=example
        with self.assertRaisesRegex(ValueError,'同段术语原词'):
            validate_writing_evidence(value,analysis)

    def test_first_use_accepts_the_explained_expression_in_a_named_anchor(self):
        item = decision('q1','src1','copy','浅拷贝（records.copy()）')
        analysis = {'mode':'answer','items':[item]}
        text = 'records.copy() 新建外层列表，两份列表仍指向相同的记录。'
        value = {'answers':[{'question_id':'q1','parts':[{'id':'p1','markdown':text}]}],
                 'reader_checks':[{'addresses':'copy','part_id':'p1','quote':text}]}
        validate_writing_evidence(value,analysis)

    def test_lesson_can_show_the_example_immediately_before_naming_it(self):
        item = decision('chapter','src1','distance','直角距离')
        item['opening'] = {'orientation':'在棋盘上走到另一个格子。','first_action':'先数横向需要走几格。'}
        analysis = {'mode':'lesson','items':[item]}
        orientation = '我们要从棋盘左上角走到右下角，可以按横竖方向移动。'
        action = '先向右数四格，再向下数两格，这一趟共走了六格。'
        definition = '这种沿两个方向相加的最短路程叫直角距离。'
        part = {'id':'p1','markdown':action+'\n\n'+definition}
        value = {'explanation':{'parts':[part],'framing':{'start':[{'role':'current','markdown':orientation}]}},
                 'reader_checks':[{'addresses':address,'part_id':owner,'quote':quote}
                                  for address,owner,quote in (
                                      ('chapter-orientation','opening',orientation),
                                      ('chapter-first-action','p1',action),('distance','p1',action))]}
        validate_writing_evidence(value,analysis)
        part['markdown'] = action+'\n\n先看另一件与距离无关的事。\n\n'+definition
        with self.assertRaisesRegex(ValueError,'术语原词'):
            validate_writing_evidence(value,analysis)

    def test_question_id_cannot_become_a_required_label_in_prose(self):
        packet = analysis_packet({'mode':'answer','questions':[{'id':'q1','text':'第二次为什么会错？'}],
                                  'sources':[{'id':'src1','original':'Read from slot zero.'}]})
        kinds = analysis_schema(packet)['properties']['items']['items']['properties']['gaps']['items']['properties']['kind']['enum']
        self.assertEqual(kinds,['first_use','reasoning'])
        item = decision('q1','src1','trace','q1')
        item['gaps'][0]['kind'] = 'task_step'
        analysis = {'mode':'answer','items':[item]}
        validate_analysis(packet,analysis)
        self.assertEqual(item['gaps'][0]['kind'],'reasoning')

    def test_math_markers_do_not_hide_the_original_variable(self):
        item = decision('q1','src1','root','源点 s')
        analysis = {'mode':'answer','items':[item]}
        content = r'以源点 \(s\) 为根，再沿基准树展开。'
        value = {'answers':[{'question_id':'q1','parts':[{'id':'p1','markdown':content}]}],
                 'reader_checks':[{'addresses':'root','part_id':'p1','quote':'以源点 $s$ 为根，再沿基准树展开。'}]}
        validate_writing_evidence(value,analysis)

    def test_accepts_an_exact_bilingual_term_name_when_the_body_uses_chinese(self):
        item = decision('q1','src1','point','Steiner 点')
        analysis = {'mode':'answer','items':[item]}
        paragraph = '新增斯坦纳点后，两条线在分叉处之前可以共享一段走线。'
        value = {'answers':[{'question_id':'q1','parts':[{'id':'p1','markdown':paragraph}]}],
                 'terms':[{'name':'斯坦纳点（Steiner Point）'}],
                 'reader_checks':[{'addresses':'point','part_id':'p1','quote':paragraph}]}
        validate_writing_evidence(value,analysis)
        value['terms'].append({'name':'另一个点（Steiner Candidate）'})
        with self.assertRaisesRegex(ValueError,'同段术语原词'):
            validate_writing_evidence(value,analysis)

    def test_run_persists_both_usage_stages_and_the_combined_total(self):
        def receipt(tokens,seconds,session):
            return {"usage":{"input_tokens":tokens,"model_calls":1},"requests":[{"input_tokens":tokens}],
                    "http_requests":1,"seconds":seconds,"usage_complete":True,"session_id":session,
                    "model_started_at":"2026-11-12T00:00:00Z","model_completed_at":"2026-11-12T00:00:05Z"}
        usage=combined_stage_usage(receipt(300,2,"analysis"),receipt(700,3,"writer"))
        with tempfile.TemporaryDirectory() as root:
            store=Runs(root)
            row=store.create("test",{"questions":[{"id":"q1"}],"groups":[["q1"]],"writer":{}})
            stored=store.update(row["id"],{**usage,"group_id":"1","type":"usage"})
            group=Runs.public(stored)["groups"][0]
        self.assertEqual(group["usage"]["input_tokens"],1000)
        self.assertEqual(group["usage"]["model_calls"],2)
        self.assertEqual(group["stages"]["analysis"]["usage"]["input_tokens"],300)
        self.assertEqual(group["stages"]["writing"]["usage"]["input_tokens"],700)
