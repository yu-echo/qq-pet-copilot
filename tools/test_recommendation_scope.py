"""Recommended-user actions remain independent of the growth-bag-only gate."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace as NS
import numpy as np
from scenarios.visit import VisitScenario
from scenarios.friend_care import FriendCareScenario
from src.moneybag import confirmed_friend_rows

class RecommendationScopeTests(unittest.TestCase):
    def setUp(self):
        self.gate=patch('src.moneybag.confirmed_friend_rows',side_effect=AssertionError('Other actions must not use bag gate')).start()
        patch('scenarios.visit.time.sleep').start()
        patch('scenarios.friend_care.time.sleep').start()
        self.addCleanup(patch.stopall)

    def test_shared_list_keeps_recommended_entry_and_can_switch(self):
        s=VisitScenario.__new__(VisitScenario)
        s.dev=Mock();s.click=Mock()
        s.dev.d.xpath.return_value.all.return_value=[NS(bounds=(10,10,60,60),attrib={'content-desc':'好友 真实好友'}),NS(bounds=(10,100,60,150),attrib={'content-desc':'好友 推荐用户'})]
        s._friends=['好友 真实好友'];s._friend_index=0
        self.assertTrue(s.next_friend())
        s.click.assert_called_once_with(35,125)
        self.gate.assert_not_called()

    def test_step_still_clicks_on_recommended_home(self):
        s=VisitScenario.__new__(VisitScenario);s.dev=Mock();s.click=Mock()
        s.see=Mock(side_effect=lambda name,**kw: (90,120) if name=='visit_step' else None)
        self.assertEqual(s.step_once(),'stepped')
        s.click.assert_called_once_with(90,120)
        self.gate.assert_not_called()

    def test_bag_filter_does_not_mutate_shared_rows(self):
        rows=[NS(bounds=(1,100,50,150)),NS(bounds=(1,700,50,750))]
        original=list(rows)
        with patch('src.ocr.ocr_texts',return_value=[('他们都在玩',10,500,.99)]):
            self.assertEqual(confirmed_friend_rows(np.zeros((900,1080,3),dtype=np.uint8),rows),rows[:1])
        self.assertEqual(rows,original)

if __name__=='__main__':unittest.main()
