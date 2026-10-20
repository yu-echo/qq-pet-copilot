"""每日活动计划的离线单元测试：parse_plan / _normalize_plan_item /
plan_to_raw / save_plan / _duration_seconds。不连设备、不写真实 config.yaml。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.settings as settings_io
from src.config import (
    PlanConfig,
    PlanItemConfig,
    SchoolConfig,
    WorkConfig,
    AdventureConfig,
    _normalize_plan_item as normalize,
    parse_plan,
)


class ParsePlanTests(unittest.TestCase):
    def test_default_plan_from_legacy_sections(self):
        """没有 plan 段时按旧 school/work/adventure 分段生成三条默认计划。"""
        plan = parse_plan(None, SchoolConfig(), WorkConfig(), AdventureConfig())
        self.assertTrue(plan.enabled)
        self.assertEqual([i.activity for i in plan.items], ['school', 'work', 'adventure'])
        for item in plan.items:
            self.assertEqual(item.mode, 'count')
            self.assertEqual(item.count, 1)

    def test_legacy_times_zero_migrates_to_once(self):
        """旧配置 times_per_day=0 表示不限，迁移成 1 次（0 次会让计划永不前进）。"""
        plan = parse_plan(None, SchoolConfig(times_per_day=0),
                          WorkConfig(times_per_day=0), AdventureConfig(times_per_day=0))
        self.assertEqual([i.count for i in plan.items], [1, 1, 1])

    def test_raw_plan_round_trip(self):
        raw = [{'activity': 'work', 'mode': 'duration', 'count': 2, 'hours': 1.5,
                'attribute': '', 'work_location': '风铃旅社', 'work_duration': '45分钟',
                'adventure_duration': ''}]
        plan = parse_plan(raw, SchoolConfig(), WorkConfig(), AdventureConfig())
        self.assertEqual(len(plan.items), 1)
        item = plan.items[0]
        self.assertEqual((item.activity, item.mode, item.count, item.hours),
                         ('work', 'duration', 2, 1.5))
        self.assertEqual((item.work_location, item.work_duration), ('风铃旅社', '45分钟'))
        # plan_to_raw 后再 parse 回来，字段一致
        again = parse_plan(settings_io.plan_to_raw(plan.items),
                           SchoolConfig(), WorkConfig(), AdventureConfig())
        self.assertEqual(settings_io.plan_to_raw(again.items), raw)

    def test_normalize_rejects_unknown_keys_and_values(self):
        """多余键被丢弃、非法值回退，不会让 load_config 抛 TypeError。"""
        item = normalize({'activity': '钓鱼', 'mode': '按心情', 'count': -5,
                          'hours': 'abc', 'attribute': '运气', 'extra': 1})
        self.assertEqual(item['activity'], 'school')
        self.assertEqual(item['mode'], 'count')
        self.assertEqual(item['count'], 1)
        self.assertEqual(item['hours'], 1.0)
        self.assertEqual(item['attribute'], '运气')
        self.assertNotIn('extra', item)

    def test_plan_enabled_top_level_key(self):
        plan = parse_plan(None, SchoolConfig(), WorkConfig(), AdventureConfig())
        plan.enabled = False
        self.assertIsInstance(plan.enabled, bool)


class SavePlanTests(unittest.TestCase):
    def test_save_plan_updates_only_plan_section(self):
        """save_plan 只改 plan 段：其余段落与注释原样保留（用临时文件模拟）。"""
        original = """# 顶部注释
schedule:
  check_interval: 15  # 行内注释

care:
  energy_threshold: 60

plan:
- activity: school
  mode: count
  count: 1
  hours: 1.0
  attribute: 力量
  work_location: ''
  work_duration: ''
  adventure_duration: ''
plan_enabled: true
"""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'config.yaml'
            target.write_text(original, encoding='utf-8')
            config_file = settings_io.CONFIG_FILE
            settings_io.CONFIG_FILE = target
            try:
                items = [PlanItemConfig(activity='adventure', mode='count', count=3,
                                        adventure_duration='45秒')]
                settings_io.save_plan(items)
            finally:
                settings_io.CONFIG_FILE = config_file
            text = target.read_text(encoding='utf-8')
            self.assertIn('# 顶部注释', text)
            self.assertIn('check_interval: 15  # 行内注释', text)
            self.assertIn('energy_threshold: 60', text)
            self.assertIn('- activity: adventure', text)
            self.assertIn('count: 3', text)
            self.assertIn('plan_enabled: true', text)


class DurationSecondsTests(unittest.TestCase):
    def test_duration_seconds(self):
        import main as gui
        self.assertEqual(gui._duration_seconds('45秒'), 45)
        self.assertEqual(gui._duration_seconds('10分钟'), 600)
        self.assertEqual(gui._duration_seconds('2小时'), 7200)
        self.assertEqual(gui._duration_seconds(' 90 分钟 '), 5400)
        self.assertEqual(gui._duration_seconds(''), 0)
        self.assertEqual(gui._duration_seconds('abc'), 0)
        self.assertEqual(gui._duration_seconds(None), 0)


if __name__ == '__main__':
    unittest.main()
