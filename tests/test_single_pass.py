import contextlib
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import collect_all_groups as c
import collection_store as store

class SinglePassTests(unittest.TestCase):
    def test_vision_result_order_jitter_does_not_mean_viewport_changed(self):
        rows=[{'text':'发言一','x':.3,'y':.8},{'text':'发言二','x':.3,'y':.6},{'text':'发言三','x':.3,'y':.4}]
        self.assertEqual(c.DesktopAdapter.signature({'lines':rows}),c.DesktopAdapter.signature({'lines':rows[::-1]}))
    def test_repeated_names_are_not_removed(self):
        chronological=['first','speaker','anchor','speaker','new statement','speaker']
        self.assertEqual(c.chain_after_anchor([list(reversed(chronological))],['first','speaker','anchor']),['speaker','new statement','speaker'])
    def test_repeated_anchor_is_ambiguous(self):
        self.assertIsNone(c.chain_after_anchor([list(reversed(['a','b','c','a','b','c']))],['a','b','c']))
    def test_ordered_overlap_and_hole(self):
        chain=[['new','e','d','c'],['d','c','b','a']]
        self.assertEqual(c.chain_after_anchor(chain,['a','b','c']),['d','e','new'])
        self.assertIsNone(c.chain_after_anchor([['new','x'],['c','b','a']],['a','b','c']))
    def test_save_reading_once_without_losing_excerpt_or_newer_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            cid=store.append_capture(root,'g0004','示例',['这句话保留原意'],'时间未知')
            batch=store.due(root)['captures'][0]
            later=store.append_capture(root,'g0001','示例2',['后来消息'],'时间未知')
            result={'readings':[{'capture_id':cid,'source_sha256':batch['sha256'],'text':'这里是简短整理。'}]}
            store.commit(root,result);store.commit(root,result)
            text=(root/'data/groups/g0004/summary.md').read_text()
            self.assertEqual(text.count('这里是简短整理。'),1)
            self.assertIn('这句话保留原意',text)
            self.assertLess(text.index('简要整理'),text.index('OCR 摘抄'))
            self.assertEqual([x['capture_id'] for x in store.due(root)['captures']],[later])
            result['readings'][0]['source_sha256']='wrong'
            with self.assertRaises(ValueError):store.commit(root,result)
    def test_only_selected_group_is_visited(self):
        with tempfile.TemporaryDirectory() as directory,contextlib.ExitStack() as stack:
            root=Path(directory)
            state={'gid':'g0001'}; clicked=[];saved=[]
            groups=(('g0001','一群'),('g0002','二群'),('g0003','三群'))
            titles=dict(groups)
            def capture():return {'x':0,'y':40,'lines':[{'text':titles[state['gid']]}]}
            def click(window,line):state['gid']=line['id'];clicked.append(line['id'])
            replacements={'ROOT':root,'SEEN':root/'data/monitor/seen.json','GROUPS':groups,
                'front_app':lambda:'WeChat','capture':capture,'load_seen':lambda:{g:{'anchor':['a','b','c']} for g,_ in groups},
                'sidebar_line':lambda w,k:{'id':next(g for g,t in groups if t==k)},'click_line':click,
                'ordered_texts':lambda w:['new','c','b','a'],'append_group_summary':lambda g,t,lines:saved.append(g)}
            def switch(w,key):click(w,{'id':next(g for g,t in groups if t==key)});return capture()
            replacements['switch_to']=switch
            for key,val in replacements.items():stack.enter_context(patch.object(c,key,val))
            stack.enter_context(patch.object(c.base,'open_title',lambda lines:lines[0]['text']))
            stack.enter_context(patch.object(c.base,'field_for_group',lambda lines,key:''))
            stack.enter_context(patch.object(c.time,'sleep',lambda _:None))
            stack.enter_context(patch.object(sys,'argv',['collect','--groups','g0002']))
            result=c.main()
            self.assertEqual(result['visits'],['g0002'])
            self.assertEqual(saved,['g0002'])
            self.assertEqual(clicked,['g0002','g0001']) # one visit plus restore

class BackfillTests(unittest.TestCase):
    def test_backfill_stays_in_affected_group_and_preserves_order(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root=Path(directory);state={'gid':'g0001','older':False};clicked=[];saved=[]
            groups=(('g0001','一群'),('g0002','二群'),('g0003','三群')); titles=dict(groups)
            def capture():return {'x':0,'y':40,'lines':[{'text':titles[state['gid']]}]}
            def click(window,line):state['gid']=line['id'];clicked.append(line['id'])
            def scroll(window,amount):state['older']=amount>0
            def texts(window):return ['d','c','b','a'] if state['older'] else ['new','d','c']
            replacements={'ROOT':root,'SEEN':root/'data/monitor/seen.json','GROUPS':groups,
                'front_app':lambda:'WeChat','capture':capture,'load_seen':lambda:{'g0002':{'anchor':['a','b','c']}},
                'sidebar_line':lambda w,k:{'id':next(g for g,t in groups if t==k)},'click_line':click,
                'ordered_texts':texts,'scroll_chat':scroll,'focus_message_list':lambda w:None,
                'append_group_summary':lambda g,t,lines:saved.append((g,lines))}
            def switch(w,key):click(w,{'id':next(g for g,t in groups if t==key)});return capture()
            replacements['switch_to']=switch
            text_values=texts
            class Adapter:
                def __init__(self,*args):pass
                texts=staticmethod(text_values)
                def preflight(self,w):return capture()
                def move(self,w,pixels):scroll(w,pixels);return capture()
                def check(self,*args):pass
                def archive(self,*args):pass
                def wait_for_history(self,*args):pass
            replacements['DesktopAdapter']=Adapter
            replacements['pin_latest']=lambda w,key:w
            for key,val in replacements.items():stack.enter_context(patch.object(c,key,val))
            stack.enter_context(patch.object(c.base,'open_title',lambda lines:lines[0]['text']))
            stack.enter_context(patch.object(c.base,'field_for_group',lambda lines,key:''))
            stack.enter_context(patch.object(c.time,'sleep',lambda _:None))
            stack.enter_context(patch.object(sys,'argv',['collect','--groups','g0002']))
            result=c.main()
            self.assertEqual(result['visits'],['g0002'])
            self.assertEqual(saved,[('g0002',['d','new'])])
            self.assertEqual(clicked,['g0002','g0001'])
            seen=json.loads(c.SEEN.read_text())['groups']['g0002']
            self.assertEqual(seen['anchor'],['c','d','new'])

if __name__=='__main__':unittest.main()
