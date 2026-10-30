import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import capture_engine as e


class Fixture:
    def __init__(self, pages, fail=None):
        self.pages=pages;self.index=0;self.moves=[];self.saved=[];self.fail=fail
    @staticmethod
    def texts(w):return w['texts']
    def preflight(self,w):
        if self.fail=='preflight':raise RuntimeError('unexpected viewport')
        return w
    def move(self,w,pixels):
        self.moves.append(pixels)
        if self.fail=='timeout':raise TimeoutError('unknown motion result')
        self.index=min(len(self.pages)-1,self.index+1)
        return self.pages[self.index]
    def check(self,w,previous):
        if w.get('title','chat')!='chat' or not e.same_layout(w,previous):raise RuntimeError('changed chat/layout')
    def archive(self,w,purpose):self.saved.append(purpose)
    def wait_for_history(self,n):pass


def frame(chrono,**kwargs):
    return dict(texts=list(reversed(chrono)),height=900,width=1400,window_id=1,x=0,y=40,**kwargs)


class ContinuousTests(unittest.TestCase):
    def run_capture(self,pages,anchor=('a','b','c'),fail=None,deadline=999):
        adapter=Fixture(pages,fail)
        return e.collect(pages[0],list(anchor),adapter,deadline,clock=lambda:0),adapter

    def test_continuous_append_and_frozen_endpoint(self):
        pages=[frame(['f','g','h','new']),frame(['c','d','e','f','g']),frame(['a','b','c','d','e'])]
        r,a=self.run_capture(pages)
        self.assertEqual(r['status'],'connected');self.assertEqual(r['lines'],['d','e','f','g','h','new'])
        self.assertEqual(r['accepted_screens'],3);self.assertEqual(len(a.moves),2)
        # Collected newest endpoint stays the initial frame, no chasing arrivals.
        self.assertEqual(r['screens'][0],pages[0]['texts'])

    def test_same_sender_repetition_not_global_dedup(self):
        r,_=self.run_capture([frame(['a','b','c','same','speaker','same','speaker'])])
        self.assertEqual(r['lines'],['same','speaker','same','speaker'])

    def test_whole_anchor_repeated_is_not_committed(self):
        r,a=self.run_capture([frame(['a','b','c','a','b','c'])])
        self.assertEqual(r['status'],'ambiguous');self.assertIsNone(r['lines']);self.assertFalse(a.moves)

    def test_one_line_overlap_is_not_sufficient(self):
        self.assertEqual(e.overlap_size(['a','shared'],['shared','new']),0)

    def test_recover_intermediate_without_losing_initial_newest(self):
        pages=[frame(['f','g','h','new']),frame(['a','b','c']),frame(['c','d','e','f','g']),frame(['a','b','c','d','e'])]
        r,a=self.run_capture(pages)
        self.assertEqual(r['status'],'connected');self.assertEqual(r['recovery_scrolls'],1)
        self.assertLess(a.moves[1],0);self.assertEqual(r['lines'],['d','e','f','g','h','new'])

    def test_gap_does_not_become_success_just_because_anchor_appears(self):
        r,_=self.run_capture([frame(['x','y','new']),frame(['a','b','c'])])
        self.assertEqual(r['status'],'hole');self.assertIsNone(r['lines'])

    def test_no_input_after_preflight_failure(self):
        r,a=self.run_capture([frame(['new','x','y'])],fail='preflight')
        self.assertEqual(r['status'],'needs_review');self.assertFalse(a.moves)

    def test_timeout_never_replays_motion(self):
        r,a=self.run_capture([frame(['new','x','y'])],fail='timeout')
        self.assertEqual(r['status'],'needs_review');self.assertEqual(len(a.moves),1)

    def test_wrong_chat_and_resize_stop(self):
        for f in [frame(['a','b','c'],title='wrong'),dict(frame(['a','b','c']),width=1200)]:
            r,a=self.run_capture([frame(['new','x','y']),f])
            self.assertEqual(r['status'],'needs_review');self.assertEqual(len(a.moves),1)

    def test_stall_and_budget_are_incomplete(self):
        r,a=self.run_capture([frame(['x','y','z'])])
        self.assertEqual(r['status'],'boundary_without_anchor');self.assertEqual(len(a.moves),3)
        r,a=self.run_capture([frame(['x','y','z'])],deadline=0)
        self.assertEqual(r['status'],'budget_exhausted');self.assertFalse(a.moves)

    def test_adaptive_step_grows_and_shrinks_with_bounds(self):
        p=e.AdaptiveStep(700);old=p.value;p.accepted(6);self.assertGreater(p.value,old)
        p.shrink();self.assertLess(p.value,old)
        for _ in range(30):p.accepted(10)
        self.assertEqual(p.value,p.maximum)

    def test_resume_preserves_newest_endpoint_and_earlier_accepted_pages(self):
        saved=[frame(['f','g','h','new'])['texts'],frame(['c','d','e','f','g'])['texts']]
        pages=[frame(['c','d','e','f','g']),frame(['a','b','c','d','e'])]
        a=Fixture(pages)
        r=e.collect(pages[0],['a','b','c'],a,999,clock=lambda:0,resume_screens=saved)
        self.assertEqual(r['status'],'connected');self.assertEqual(r['lines'],['d','e','f','g','h','new'])
        self.assertEqual(len(a.moves),1)

    def test_resume_frontier_changed_never_moves(self):
        saved=[frame(['f','g','h','new'])['texts']]
        f=frame(['other','viewport','here']);a=Fixture([f])
        r=e.collect(f,['a','b','c'],a,999,clock=lambda:0,resume_screens=saved)
        self.assertEqual(r['status'],'resume_frontier_changed');self.assertFalse(a.moves)

    def test_ocr_tolerance_is_not_used_for_incremental_commits(self):
        text='这是一条足够长用于验证重叠文字的真实发言记录'
        self.assertEqual(e.overlap_size(['name',text+'心'],['name',text]),0)
        self.assertEqual(e.overlap_size(['name',text+'心'],['name',text],True),2)


if __name__=='__main__':unittest.main()
