"""三轮验收共用的离线功能测试。设备输入、外部通知均为桩；进度写临时目录。"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import onnxruntime  # 与打包 runtime hook 一致：必须在 Qt 前初始化 ORT。
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.test_care_regression import CareRegression
from src.ocr import ocr_fullscreen, parse_panel_location, parse_employed_remaining
from src.settings import WORK_LOCATIONS, validate_field
from src import progress_store as store
from src.config import (MAIN_TASK_KEYS, Config, PlanConfig, PlanItemConfig,
                        load_config)
from scenarios.work import WorkScenario
from scenarios.school import SchoolScenario
from scenarios.adventure import AdventureScenario
from scenarios.visit import VisitScenario
from scenarios.pk import PKScenario, PK_ROUND_CAP
from scenarios.friend_care import FriendCareScenario, parse_time_range, in_time_range
from scenarios.hire_friend import FriendHireScenario
from scenarios.employed import EmployedScenario
from scenarios.runner import Runner, TaskQueueRunner, _QueueTask, ScenarioFailed
from src.scenario import TaskDeferred

WIDTH = int(os.environ.get('TEST_WIDTH', '1080'))


def work_rows(location='云朵梦舍'):
    rows = [('力量784',260,284,1), ('智力883',566,285,1), ('魅力3209',872,282,1),
            ('闪耀星屋',906,344,1), (location,466,570,1), ('规则说明',880,890,1),
            ('雇佣有额外加成',830,1450,1), ('去打工',540,2040,1)]
    return [(t,x*WIDTH/1080,y*WIDTH/1080,s) for t,x,y,s in rows]


class FullRegression(unittest.TestCase):
    def test_packaged_runtime_uses_shared_adb_and_limited_threads(self):
        import runpy
        from adbutils._utils import adb_path
        with tempfile.TemporaryDirectory() as folder:
            adb=Path(folder)/'resources/scrcpy-win64/adb.exe'
            adb.parent.mkdir(parents=True);adb.touch()
            with patch.dict(os.environ,{},clear=True), patch.object(sys,'frozen',True,create=True), patch.object(sys,'_MEIPASS',folder,create=True):
                runpy.run_path('tools/pyi_rth_preload_onnxruntime.py')
                self.assertEqual(adb_path(),str(adb))
                self.assertEqual(os.environ['OPENBLAS_NUM_THREADS'],'1')

    def test_config_cache_refresh_and_mutation_isolation(self):
        from src import config
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.yaml'
            path.write_text('work:\n  duration: 10分钟\n',encoding='utf-8')
            config._read_config_cached.cache_clear()
            first=load_config(path);first.work.duration='2小时'
            self.assertEqual(load_config(path).work.duration,'10分钟')
            self.assertEqual(config._read_config_cached.cache_info().hits,1)
            replacement=path.with_suffix('.tmp')
            replacement.write_text('work:\n  duration: 45分钟\n',encoding='utf-8');replacement.replace(path)
            self.assertEqual(load_config(path).work.duration,'45分钟')

    def test_queue_unchanged_write_throttling_and_recreation(self):
        from src import queue_status as queue
        with tempfile.TemporaryDirectory() as folder, patch.object(queue,'_last_write',None):
            path=Path(folder)/'queue.json'
            with patch.object(queue,'QUEUE_STATUS_FILE',path), patch.object(queue.time,'monotonic',return_value=1):
                queue.save_queue_status({'current':'work','updated':'a'})
                queue.save_queue_status({'current':'work','updated':'b'})
                self.assertEqual(queue.load_queue_status()['updated'],'a')
                queue.save_queue_status({'current':'care','updated':'c'})
                self.assertEqual(queue.load_queue_status()['current'],'care')
                path.unlink()
                queue.save_queue_status({'current':'care','updated':'d'})
                self.assertTrue(path.is_file())
            with patch.object(queue,'QUEUE_STATUS_FILE',path), patch.object(queue.time,'monotonic',return_value=40):
                queue.save_queue_status({'current':'care','updated':'e'})
                self.assertEqual(queue.load_queue_status()['updated'],'e')

    def test_native_embedded_window_counts_as_foreground(self):
        import main
        window=NS(winId=lambda:100,isActiveWindow=lambda:False)
        with patch('main.win32gui.GetForegroundWindow',return_value=200), patch('main.win32gui.GetAncestor',side_effect=lambda hwnd,flag:100):
            self.assertTrue(main.window_is_foreground(window))
        with patch('main.win32gui.GetForegroundWindow',return_value=300), patch('main.win32gui.GetAncestor',side_effect=lambda hwnd,flag:hwnd):
            self.assertFalse(main.window_is_foreground(window))

    def test_legacy_config_ignores_removed_emulator_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.yaml'
            path.write_text('emulator:\n  type: MuMuPlayer12\nrecover:\n  method: 重启游戏\n  emulator_restart_cmd: obsolete\n',encoding='utf-8')
            cfg=load_config(path)
            self.assertFalse(hasattr(cfg,'emulator'))
            self.assertFalse(hasattr(cfg.recover,'emulator_restart_cmd'))
            self.assertEqual(cfg.recover.method,'重启游戏')

    def test_ocr_uses_only_one_cpu_thread_and_no_gpu(self):
        from src.ocr import get_engine
        engine=get_engine()
        for model in (engine.text_det,engine.text_cls,engine.text_rec):
            self.assertEqual(model.session.session.get_providers(),['CPUExecutionProvider'])
            options=model.session.session.get_session_options()
            self.assertEqual(options.intra_op_num_threads,1)
            self.assertEqual(options.inter_op_num_threads,1)

    def test_phone_recover_official_scheme(self):
        from src import recover
        adb=Mock(); adb.getprop.return_value='phone'; dev=Mock()
        with patch.object(recover,'_connect_u2',return_value=dev), patch.object(recover,'_wait_qq_settle'), patch.object(recover,'_open_pet_via_scheme') as scheme, patch.object(recover,'_wait_main_page',return_value=True):
            self.assertIs(recover.reenter_pet(adb,'重启游戏'),dev)
            adb.force_stop_app.assert_called_once()
            adb.reboot_and_wait.assert_not_called()
            scheme.assert_called_once_with(adb.adb,adb.serial)

    def test_gui_starts_phone_runner_with_lower_priority(self):
        import main
        window=NS(_runner_proc=None,_read_runner_logs=Mock())
        with patch('main.subprocess.Popen') as popen, patch('main.threading.Thread'):
            main.MainWindow.start_runner(window)
            self.assertTrue(popen.call_args.kwargs['creationflags'] & main.subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            self.assertFalse(any('emulator' in arg for arg in popen.call_args.args[0]))

    def test_background_mirror_pauses_once_and_resumes(self):
        import main
        window=NS(btn_scrcpy=Mock(), isActiveWindow=Mock(return_value=False),
                  isMinimized=Mock(return_value=False), _background_mirror_paused=False,
                  _bg_ticks=0,
                  _runner_proc=NS(poll=lambda: None),  # 调度器在跑，看门狗才工作
                  _disable_scrcpy=Mock(), _enable_scrcpy=Mock())
        window.btn_scrcpy.isChecked.return_value=True
        # 防抖：单次或不足 SCRCPY_THROTTLE_TICKS 轮的"后台"判定不触发暂停，
        # 只有连续 SCRCPY_THROTTLE_TICKS 轮都判后台才真正暂停一次。
        for _ in range(main.SCRCPY_THROTTLE_TICKS - 1):
            main.MainWindow._check_scrcpy(window)
        window._disable_scrcpy.assert_not_called()
        main.MainWindow._check_scrcpy(window)
        window._disable_scrcpy.assert_called_once()
        window._enable_scrcpy.assert_not_called()
        window.isActiveWindow.return_value=True
        main.MainWindow._check_scrcpy(window)
        window._enable_scrcpy.assert_called_once()
        self.assertFalse(window._background_mirror_paused)

    def test_background_manual_mirror_off_stays_off(self):
        import main
        window=NS(btn_scrcpy=Mock(), _background_mirror_paused=True,
                  _disable_scrcpy=Mock(), _enable_scrcpy=Mock())
        window.btn_scrcpy.isChecked.return_value=False
        main.MainWindow._check_scrcpy(window)
        window._enable_scrcpy.assert_not_called()

    def test_scrcpy_watchdog_runs_before_start(self):
        # 看门狗不依赖"开始"：未跑调度器时也维护镜像（用户可能只想要画面）
        import main
        window = NS(btn_scrcpy=Mock(), _runner_proc=None,
                    _background_mirror_paused=False, _bg_ticks=0,
                    _scrcpy_retry_at=0.0, _scrcpy_proc=None,
                    _embed_tries=0, _embed_fail_logged=False,
                    _embed_timer=NS(isActive=lambda: False, start=Mock()),
                    scrcpy_view=Mock(),
                    isActiveWindow=Mock(return_value=True),
                    isMinimized=Mock(return_value=False),
                    _disable_scrcpy=Mock(), _enable_scrcpy=Mock())
        window.btn_scrcpy.isChecked.return_value = True
        # scrcpy.exe 是构建期才下载的第三方二进制（.gitignore 排除，不入库），
        # 用例里用临时文件顶替，否则干净检出（没有该目录）时必然失败。
        with tempfile.TemporaryDirectory() as folder:
            fake_scrcpy = Path(folder) / 'scrcpy.exe'
            fake_scrcpy.touch()
            with patch.object(main, 'SCRCPY', fake_scrcpy), \
                 patch('main.start_scrcpy', return_value=Mock()) as start:
                main.MainWindow._check_scrcpy(window)
        start.assert_called_once()

    def test_svip_entry_template_match(self):
        # 入口用模板匹配（同福袋套路：归一 1080 宽 + ROI 内多尺寸 matchTemplate），
        # 不写死像素坐标；合成一张贴图验证能命中且位置正确
        import cv2
        from scenarios.svip import find_entry_icon
        from src.config import resource_path
        tpl = cv2.imdecode(
            np.fromfile(resource_path('resources/svip-entry.png'), dtype=np.uint8),
            cv2.IMREAD_GRAYSCALE)
        screen = np.full((1200, 1080, 3), 240, dtype=np.uint8)
        size = 70
        tx, ty = 900, 300  # 完整落在右侧图标列 ROI（x 880~1040, y 200~560）内
        screen[ty:ty + size, tx:tx + size] = cv2.cvtColor(
            cv2.resize(tpl, (size, size)), cv2.COLOR_GRAY2RGB)
        hit = find_entry_icon(screen)
        self.assertIsNotNone(hit)
        cx, cy, score = hit
        self.assertGreater(score, 0.90)
        self.assertLess(abs(cx - (tx + size // 2)), 10)
        self.assertLess(abs(cy - (ty + size // 2)), 10)
        # 空白屏不应命中
        self.assertIsNone(find_entry_icon(np.full((1200, 1080, 3), 240, dtype=np.uint8)))

    def test_moneybag_coins_accumulate_and_reset(self):
        # 福袋金币累计：同账号累加；换账号/配置变更清零重计
        import src.progress as prog
        from src.moneybag import parse_coins
        self.assertEqual(parse_coins('成长福袋获得120金币'), 120)
        self.assertEqual(parse_coins('获得 1,000 金币'), 1000)
        self.assertEqual(parse_coins('没有数字'), 0)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'moneybag_stats.json'
            with patch.object(prog, 'MONEYBAG_STATS_FILE', f), \
                 patch.object(prog, '_moneybag_config_sig', return_value='sig-1'):
                prog.add_moneybag_coins(50, pet_name='A')
                st = prog.add_moneybag_coins(70, pet_name='A')
                self.assertEqual(st['coins'], 120)
                self.assertEqual(st['bags'], 2)
                self.assertEqual(prog.load_moneybag_stats()['coins'], 120)
                st = prog.add_moneybag_coins(10, pet_name='B')  # 换账号 -> 清零
                self.assertEqual(st['coins'], 10)
                self.assertEqual(st['pet_name'], 'B')
            with patch.object(prog, 'MONEYBAG_STATS_FILE', f), \
                 patch.object(prog, '_moneybag_config_sig', return_value='sig-2'):
                st = prog.add_moneybag_coins(5, pet_name='B')  # 配置变更 -> 清零
                self.assertEqual(st['coins'], 5)


    def test_disable_scrcpy_hands_off_before_killing_mirror(self):
        import main
        # 切后台（permanent=False）：镜像常驻不杀、只藏窗口，不启动无头关屏。
        # scrcpy 被杀才是闪屏根因；常驻后切换时屏幕状态不变。
        window = NS(_screen_off_proc=None,
                    _scrcpy_proc=Mock(),
                    _embed_timer=Mock(),
                    scrcpy_view=Mock())
        with patch('main.start_scrcpy_screen_off') as ss_off, \
             patch('main.kill_our_scrcpy') as kill:
            main.MainWindow._disable_scrcpy(window, permanent=False)
        ss_off.assert_not_called()
        kill.assert_not_called()
        window.scrcpy_view.unembed.assert_called_once()
        # 手动关镜像开关（permanent=True）：杀镜像 + 启用无头关屏。
        screen_off = Mock(); screen_off.poll.return_value = None
        window2 = NS(_screen_off_proc=None,
                     _scrcpy_proc=Mock(),
                     _embed_timer=Mock(),
                     scrcpy_view=Mock())
        with patch('main.start_scrcpy_screen_off', return_value=screen_off) as ss_off, \
             patch('main.kill_our_scrcpy') as kill, \
             patch('main.time.sleep') as _sleep:
            main.MainWindow._disable_scrcpy(window2, permanent=True)
        ss_off.assert_called_once()
        kill.assert_called_once()
        self.assertIs(window2._screen_off_proc, screen_off)

    def test_enable_scrcpy_keeps_screen_off_if_mirror_fails(self):
        import main
        # 镜像没拉起来时不能把无头关屏进程收掉，否则屏幕被放亮。
        screen_off = Mock(); screen_off.poll.return_value = None
        window = NS(_background_mirror_paused=False,
                    _scrcpy_proc=None,
                    _screen_off_proc=screen_off,
                    _embed_tries=0, _embed_fail_logged=False,
                    _embed_timer=Mock(),
                    scrcpy_view=Mock())
        with patch('main.start_scrcpy', return_value=None) as ss, \
             patch('main.time.sleep') as _sleep:
            main.MainWindow._enable_scrcpy(window)
        ss.assert_called_once()
        screen_off.terminate.assert_not_called()
        self.assertIs(window._screen_off_proc, screen_off)

    def test_background_skips_stats_file_reads(self):
        import main
        window=NS(isActiveWindow=Mock(return_value=False))
        with patch('main.load_config',side_effect=AssertionError('后台不读配置')):
            main.MainWindow._refresh_stats(window)

    def test_tracked_mirror_stop_does_not_scan_other_processes(self):
        import main
        proc=Mock(); proc.poll.return_value=None
        with patch('main._kill_scrcpy_by_marker',side_effect=AssertionError('不应扫描')):
            main.kill_our_scrcpy(proc)
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once_with(timeout=2)

    def test_hire_work_records_actual_duration_for_settlement(self):
        from src import progress
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'work.json'
            for duration,seconds in [('10分钟',600),('45分钟',2700),('2小时',7200)]:
                for deferred in (False,True):
                    with self.subTest(duration=duration,deferred=deferred):
                        target.unlink(missing_ok=True)
                        sc=FriendHireScenario.__new__(FriendHireScenario)
                        sc.dev=Mock(); sc.defer_wait=deferred
                        sc.cfg=NS(work=NS(location='风铃旅社',duration=duration))
                        for method in ('_enter_work_panel','_select_job','defer_busy_end','ensure_main_page'):
                            setattr(sc,method,Mock())
                        work=Mock(duration=duration)
                        with patch('scenarios.hire_friend.WorkScenario',return_value=work), patch.object(progress,'WORK_PROGRESS_FILE',target):
                            sc._hire_and_work()
                            self.assertEqual(progress.get_current_work_duration(),duration)
                            self.assertEqual(progress.record_work_finish(),seconds)

    def test_work_eight_locations_with_neighbour(self):
        for location in WORK_LOCATIONS:
            with self.subTest(location=location):
                self.assertEqual(parse_panel_location(work_rows(location), WIDTH), location)

    def test_work_map_without_panel_rejected(self):
        self.assertIsNone(parse_panel_location(work_rows()[:5], WIDTH))

    def test_work_missing_or_ambiguous_name_rejected(self):
        rows=work_rows('未知建筑')
        self.assertIsNone(parse_panel_location(rows, WIDTH))
        rows=work_rows()+[('彩虹画室',610*WIDTH/1080,570*WIDTH/1080,1)]
        self.assertIsNone(parse_panel_location(rows, WIDTH))

    def test_work_real_failure_screenshots(self):
        names = ('alert_20260908_222232.png','error_retry1_20260908_222005.png',
                 'error_retry1_20260908_222448.png')
        root = Path(__file__).resolve().parents[2]/'runs'
        missing = [name for name in names if not (root/name).is_file()]
        if missing:
            self.skipTest('历史异常截图缺失，不能验证原现场: ' + ', '.join(missing))
        for name in names:
            path=root/name
            with self.subTest(image=name):
                im=Image.open(path).convert('RGB')
                im=im.resize((WIDTH, round(im.height*WIDTH/im.width)))
                self.assertEqual(parse_panel_location(ocr_fullscreen(np.array(im)), WIDTH),'云朵梦舍')

    def test_work_retained_navigation_screenshot(self):
        # 固定素材保存在测试目录，避免依赖可清理的运行截图。
        path = Path(__file__).parent/'fixtures'/'work-cloud-panel.png'
        if not path.is_file():
            self.skipTest('公开源码不附带维护者的游戏截图；本地保留素材时执行此项')
        im = Image.open(path).convert('RGB')
        im = im.resize((WIDTH, round(im.height*WIDTH/im.width)))
        self.assertEqual(parse_panel_location(ocr_fullscreen(np.array(im)), WIDTH), '云朵梦舍')

    def test_work_correct_panel_does_not_back_or_click(self):
        work=WorkScenario.__new__(WorkScenario)
        work.location='云朵梦舍'
        work.screen=lambda:np.zeros((WIDTH*2,WIDTH,3),dtype=np.uint8)
        work.go_back=Mock(side_effect=AssertionError('正确面板不应返回'))
        work.click=Mock(side_effect=AssertionError('正确面板不应重新点地图'))
        with patch('scenarios.work.ocr_fullscreen',return_value=work_rows()), patch('scenarios.work.time.sleep'):
            work.select_place()

    def test_work_wrong_place_not_accepted(self):
        work=WorkScenario.__new__(WorkScenario)
        work.location='彩虹画室'
        work.screen=lambda:np.zeros((WIDTH*2,WIDTH,3),dtype=np.uint8)
        with patch('scenarios.work.ocr_fullscreen',return_value=work_rows()):
            self.assertFalse(work.is_correct_place_panel())

    def test_school_course_orders(self):
        sc=SchoolScenario.__new__(SchoolScenario)
        sc.screen=lambda:np.zeros((1280,720,3),dtype=np.uint8)
        for stage, order in [('初级学园',('力量','智力','魅力')),
                             ('中级学园',('力量','智力','魅力')),
                             ('高级学园',('魅力','力量','智力')),
                             ('进修学院',('力量','魅力','智力'))]:
            for i, attribute in enumerate(order,1):
                sc.attribute=attribute
                with self.subTest(stage=stage,attribute=attribute), patch('scenarios.school.ocr_texts',return_value=[(stage+'5年级',360,100,1)]), patch('scenarios.school.set_current_school'):
                    self.assertEqual(sc.resolve_course_box(),f'select_box_{i}')

    def test_adventure_normal_and_bad_weather_paths(self):
        for recall in (False,True):
            sc=AdventureScenario.__new__(AdventureScenario)
            sc.skip_bad_weather=True; sc.defer_wait=True; sc.duration='45秒'
            sc.click_until_gone_or_see=Mock(); sc.recall_bad_weather=Mock(return_value=recall)
            sc.select_duration=Mock()  # 时长选择单独用例覆盖，这里只测正常/召回两条路径
            sc.defer_busy_end=Mock(); sc.ensure_main_page=Mock()
            with patch('scenarios.adventure.count_cross') as count:
                self.assertEqual(sc.do_adventure(),recall)
                self.assertEqual(count.call_count,int(recall))
                self.assertEqual(sc.defer_busy_end.call_count,int(not recall))

    def test_adventure_two_hour_skips_bad_weather(self):
        # 2 小时冒险不触发"天色不对"召回：即使开关打开也不召回
        sc=AdventureScenario.__new__(AdventureScenario)
        sc.skip_bad_weather=True; sc.defer_wait=True; sc.duration='2小时'
        sc.click_until_gone_or_see=Mock(); sc.recall_bad_weather=Mock()
        sc.select_duration=Mock(); sc.defer_busy_end=Mock(); sc.ensure_main_page=Mock()
        with patch('scenarios.adventure.count_cross') as count:
            self.assertFalse(sc.do_adventure())
        sc.recall_bad_weather.assert_not_called()
        count.assert_not_called()

    def test_visit_already_and_available(self):
        for done in (True,False):
            sc=VisitScenario.__new__(VisitScenario)
            sc.dev=NS(hierarchy=lambda:None); sc.click=Mock()
            sc.see=lambda key,**kw: (100,200,1) if key==('visit_stepped' if done else 'visit_step') else None
            with patch('scenarios.visit.time.sleep'):
                self.assertEqual(sc.step_once(),'already' if done else 'stepped')
            self.assertEqual(sc.click.call_count,int(not done))

    # ---- SVIP 礼包（每日领取；非会员自动关任务） ----

    @staticmethod
    def _svip_scenario(states):
        """构造裸 SvipScenario：see 按 states 字典应答，设备交互全部 Mock。"""
        from scenarios.svip import SvipScenario
        sc = SvipScenario.__new__(SvipScenario)
        sc.click = Mock()
        sc.screen = Mock(return_value=object())
        sc.see = lambda key, *a, **kw: states.get(key)
        sc.ensure_main_page = Mock()
        sc._close_dialog = Mock()
        return sc

    def test_svip_claim_success(self):
        # 会员且今日未领：点"立即领取" -> 记进度 -> 返回 True
        sc = self._svip_scenario({'svip_entry': (10, 20, 1),
                                  'svip_dialog': (100, 200, 1),
                                  'svip_claim': (100, 300, 1)})
        with patch('scenarios.svip.time.sleep'), \
             patch('scenarios.svip.svip_claimed_today', return_value=False), \
             patch('scenarios.svip.save_svip_claim') as save:
            self.assertTrue(sc.run())
        save.assert_called_once_with(True)
        self.assertGreaterEqual(sc.click.call_count, 2)  # 入口 + 领取按钮
        sc.ensure_main_page.assert_called()

    def test_svip_already_claimed_today_skips(self):
        # 进度文件已标记今天领取：直接跳过，不碰设备
        from scenarios.svip import SvipScenario
        sc = SvipScenario.__new__(SvipScenario)
        sc.ensure_main_page = Mock()
        with patch('scenarios.svip.svip_claimed_today', return_value=True), \
             patch('scenarios.svip.save_svip_claim') as save:
            self.assertFalse(sc.run())
        save.assert_not_called()
        sc.ensure_main_page.assert_not_called()

    def test_svip_tomorrow_dialog_marks_done(self):
        # 弹窗按钮是"明日再来"：说明今天已领过，记进度并返回 False
        sc = self._svip_scenario({'svip_entry': (10, 20, 1),
                                  'svip_dialog': (100, 200, 1),
                                  'svip_tomorrow': (100, 300, 1)})
        with patch('scenarios.svip.time.sleep'), \
             patch('scenarios.svip.svip_claimed_today', return_value=False), \
             patch('scenarios.svip.save_svip_claim') as save:
            self.assertFalse(sc.run())
        save.assert_called_once_with(True)
        sc.click.assert_called_once()  # 只点了入口，没点领取

    def test_svip_non_member_disables_task(self):
        # 非会员：弹窗按钮是"开通 SVIP" -> 自动关闭任务并返回 False，不记领取进度
        sc = self._svip_scenario({'svip_entry': (10, 20, 1),
                                  'svip_dialog': (100, 200, 1),
                                  'svip_open': (100, 300, 1)})
        with patch('scenarios.svip.time.sleep'), \
             patch('scenarios.svip.svip_claimed_today', return_value=False), \
             patch('scenarios.svip.save_svip_claim') as save, \
             patch('scenarios.svip.SvipScenario._disable_task') as disable:
            self.assertFalse(sc.run())
        disable.assert_called_once()
        save.assert_not_called()

    def test_svip_reward_popup_means_claimed(self):
        # 真机 bug 回归：会员未领取时点帽图标是"直接发奖"并弹"恭喜获得"奖励页
        # （没有"立即领取"按钮），必须判定为领取成功并记进度
        sc = self._svip_scenario({'svip_entry': (10, 20, 1),
                                  'svip_dialog': (100, 200, 1),
                                  'svip_reward': (100, 300, 1)})
        with patch('scenarios.svip.save_svip_claim') as save, \
             patch.object(sc, '_close_dialog') as close:
            self.assertTrue(sc._claim_once())
        save.assert_called_once_with(True)
        close.assert_called_once()

    def test_svip_probe_membership_reward_is_member(self):
        # 探测时撞上"刚领到"的奖励页 -> 也是会员，并顺手记当天已领
        sc = self._svip_scenario({})
        sc._open_and_read_state = Mock(return_value=('reward', object()))
        with patch('scenarios.svip.save_svip_claim') as save:
            self.assertTrue(sc.probe_membership())
        save.assert_called_once_with(True)

    def test_svip_disable_task_writes_config(self):
        # _disable_task 把 tasks.svip.enabled=false 写回 config.yaml，并打非会员标记
        from scenarios.svip import SvipScenario
        data = {'tasks': {'svip': {'enabled': True}}}
        with patch('src.settings.load_raw', return_value=data), \
             patch('src.settings.save_raw') as save_raw, \
             patch('scenarios.svip.set_svip_nonmember') as mark:
            SvipScenario._disable_task()
        save_raw.assert_called_once()
        self.assertFalse(data['tasks']['svip']['enabled'])
        mark.assert_called_once_with(True)

    def test_svip_probe_membership_states(self):
        # 会员探测：明日再来/立即领取 -> True（顺手记领取进度）；开通 SVIP -> False
        sc = self._svip_scenario({'svip_entry': (10, 20, 1),
                                  'svip_dialog': (100, 200, 1),
                                  'svip_tomorrow': (100, 300, 1)})
        sc._open_and_read_state = Mock(return_value=('tomorrow', object()))
        with patch('scenarios.svip.save_svip_claim') as save:
            self.assertTrue(sc.probe_membership())
        save.assert_called_once_with(True)
        sc._open_and_read_state = Mock(return_value=('open', object()))
        self.assertFalse(sc.probe_membership())
        sc._open_and_read_state = Mock(return_value=(None, object()))
        self.assertIsNone(sc.probe_membership())  # 识别不了不当成状态变化

    def test_svip_recheck_reopens_task_for_member(self):
        # 非会员自动关闭状态：每次启动探测一次，恢复会员 -> 自动写回 enabled=true
        from scenarios.runner import TaskQueueRunner
        runner = TaskQueueRunner.__new__(TaskQueueRunner)
        runner.svip = Mock()
        runner.svip.probe_membership.return_value = True
        data = {'tasks': {'svip': {'enabled': False}}}
        with patch('scenarios.runner.svip_nonmember_flag', return_value=True), \
             patch('scenarios.runner.load_config') as load_cfg, \
             patch('src.settings.load_raw', return_value=data), \
             patch('src.settings.save_raw') as save_raw:
            load_cfg.return_value.tasks.svip.enabled = False
            runner._svip_nonmember_recheck()
        save_raw.assert_called_once()
        self.assertTrue(data['tasks']['svip']['enabled'])
        # 手动关闭（无非会员标记）：不探测
        with patch('scenarios.runner.svip_nonmember_flag', return_value=False), \
             patch('src.settings.save_raw') as save_raw2:
            runner._svip_nonmember_recheck()
        save_raw2.assert_not_called()
        # 开关本来就开着：不探测
        runner.svip.probe_membership.reset_mock()
        with patch('scenarios.runner.svip_nonmember_flag', return_value=True), \
             patch('scenarios.runner.load_config') as load_cfg2:
            load_cfg2.return_value.tasks.svip.enabled = True
            runner._svip_nonmember_recheck()
        runner.svip.probe_membership.assert_not_called()

    def test_migrate_tasks_order_appends_new_keys(self):
        # 老配置 tasks.order 缺 svip：启动迁移自动追加到队尾；已有则不写盘
        from src import settings as settings_mod
        data = {'tasks': {'order': 'care>work'}}
        with patch.object(settings_mod, 'load_raw', return_value=data), \
             patch.object(settings_mod, 'save_raw') as save_raw:
            settings_mod.migrate_tasks_order()
        save_raw.assert_called_once()
        self.assertEqual(data['tasks']['order'], 'svip>care>work')
        data2 = {'tasks': {'order': 'care>svip>work'}}
        with patch.object(settings_mod, 'load_raw', return_value=data2), \
             patch.object(settings_mod, 'save_raw') as save_raw2:
            settings_mod.migrate_tasks_order()
        save_raw2.assert_not_called()


    def test_wait_employed_back_exits_when_employment_ends(self):
        # 真机 bug 回归：雇佣自然到期回主页（面板消失）后，等待循环必须退出，
        # 不能无限空转卡死整个调度器
        from scenarios.employed import EmployedScenario
        sc = EmployedScenario.__new__(EmployedScenario)
        sc.dev = NS(click=lambda *a: None)
        sc.screen = Mock(return_value=object())
        sc.employed_recall_ready = Mock(return_value=False)
        sc.ensure_main_page = Mock()
        sc._recall_employed = Mock()
        # 前两轮还能看到"被雇佣中"，之后连续消失 -> 应退出且不召回
        states = [(10, 20, 1), (10, 20, 1), None, None, None, None]
        sc.see = lambda key, screen=None, source=None: (
            states.pop(0) if key == 'employed_in' else None)
        with patch('src.scenario.time.sleep'):
            sc.wait_employed_back(check_interval=0.01)
        sc._recall_employed.assert_not_called()
        sc.ensure_main_page.assert_called_once()

    def test_wait_employed_back_recalls_when_ready(self):
        # 召回条件满足：正常走召回，不受"面板消失"退出影响
        from scenarios.employed import EmployedScenario
        sc = EmployedScenario.__new__(EmployedScenario)
        sc.dev = NS(click=lambda *a: None)
        sc.screen = Mock(return_value=object())
        sc.employed_recall_ready = Mock(return_value=True)
        sc.see = Mock(return_value=(10, 20, 1))
        sc._recall_employed = Mock()
        sc.ensure_main_page = Mock()
        with patch('src.scenario.time.sleep'):
            sc.wait_employed_back(check_interval=0.01)
        sc._recall_employed.assert_called_once()

    def test_reset_daily_progress_for_pet(self):
        # 换宠物：当天计数清零、历史保留；没有计数时不产生重复重置项
        import src.progress as prog
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            files = {n: p / f'{n}.json' for n in
                     ('school', 'work', 'adventure', 'employed',
                      'visit', 'pk', 'hire_friend', 'exp', 'svip')}
            consts = {'SCHOOL_PROGRESS_FILE': files['school'],
                      'WORK_PROGRESS_FILE': files['work'],
                      'ADVENTURE_PROGRESS_FILE': files['adventure'],
                      'EMPLOYED_PROGRESS_FILE': files['employed'],
                      'VISIT_PROGRESS_FILE': files['visit'],
                      'PK_PROGRESS_FILE': files['pk'],
                      'HIRE_FRIEND_PROGRESS_FILE': files['hire_friend'],
                      'EXP_DAILY_PROGRESS_FILE': files['exp'],
                      'SVIP_PROGRESS_FILE': files['svip']}
            with patch.multiple(prog, **consts):
                # 用真实当天日期：进度文件按天判定，写死日期会在跨天后失效
                today_str = date.today().isoformat()
                prog.save_progress(files['school'], today_str, 3, {'2026-09-18': 2})
                prog.save_progress(files['visit'], today_str, 10, {})
                prog.save_exp_daily(True, today_str, {})
                prog.save_svip_claim(True, today_str, {})
                reset = prog.reset_daily_progress_for_pet('咕咕嘎嘎')
                self.assertIn('学习', reset)
                self.assertIn('踩踩', reset)
                self.assertIn('经验日常', reset)
                self.assertIn('SVIP礼包', reset)
                today, done, history = prog.load_progress(files['school'], quiet=True)
                self.assertEqual(done, 0)
                self.assertEqual(history.get('2026-09-18'), 2)  # 历史保留
                self.assertEqual(prog.load_progress(files['visit'], quiet=True)[1], 0)
                _, exp_done, _ = prog.load_exp_daily(quiet=True)
                self.assertFalse(exp_done)
                # 第二次调用：已无计数，不再列出
                self.assertEqual(prog.reset_daily_progress_for_pet('咕咕嘎嘎'), [])

    def test_pk_round_cap(self):
        self.assertEqual(PKScenario._round_limit(15,14),15)
        self.assertEqual(PKScenario._round_limit(0,4),4+PK_ROUND_CAP)
        self.assertLessEqual(PKScenario._round_limit(100,0),PK_ROUND_CAP)

    def test_friend_care_exp_daily_one_click(self):
        """好友护理=经验日常：照顾区域有 exp 就点一键护理（不计完成）。"""
        sc=FriendCareScenario.__new__(FriendCareScenario)
        sc.dev=NS(hierarchy=lambda:None)
        sc.screen=Mock(return_value=np.zeros((100,100,3),dtype=np.uint8)); sc.click=Mock()
        sc.see=lambda key,**kw: (10,20,1) if key in ('one_click_care','one_click_pay') else None
        with patch('scenarios.friend_care.see_bounds',return_value=(0,0,100,100)), \
             patch('scenarios.friend_care.ocr_texts',return_value=[('exp 100',0,0,1)]), \
             patch('scenarios.friend_care.time.sleep'), \
             patch('scenarios.friend_care.save_exp_daily') as save:
            self.assertTrue(sc._try_exp_daily())
        save.assert_not_called()
        self.assertGreaterEqual(sc.click.call_count,1)

    def test_friend_care_exp_daily_done_when_no_exp(self):
        """照顾区域无 exp：标记经验日常完成，不点护理。"""
        sc=FriendCareScenario.__new__(FriendCareScenario)
        sc.dev=NS(hierarchy=lambda:None)
        sc.screen=Mock(return_value=np.zeros((100,100,3),dtype=np.uint8)); sc.click=Mock()
        sc.see=lambda key,**kw: (10,20,1) if key=='one_click_care' else None
        with patch('scenarios.friend_care.see_bounds',return_value=(0,0,100,100)), \
             patch('scenarios.friend_care.ocr_texts',return_value=[('体力 90',0,0,1)]), \
             patch('scenarios.friend_care.save_exp_daily') as save:
            self.assertFalse(sc._try_exp_daily())
        save.assert_called_once_with(True)
        sc.click.assert_not_called()

    def test_friend_hire_busy_defers_without_visiting(self):
        sc=FriendHireScenario.__new__(FriendHireScenario)
        sc.cfg=NS(hire_friend=NS(enabled=True))
        sc.ensure_main_page=Mock(); sc.detect_busy_remaining=Mock(return_value=('work',600))
        sc.goto_first_friend=Mock()
        with self.assertRaises(TaskDeferred):
            sc.run(max_rounds=1)
        sc.goto_first_friend.assert_not_called()

    def test_employed_recall_policy(self):
        for ready in (False,True):
            sc=EmployedScenario.__new__(EmployedScenario)
            sc.cfg=NS(employed=NS(action='等到25/75'))
            sc.ensure_main_page=Mock(); sc.leave_home=Mock(return_value=None); sc.screen=Mock()
            sc.see=Mock(return_value=True); sc.employed_recall_ready=Mock(return_value=ready)
            sc._recall_employed=Mock()
            self.assertTrue(sc.run())
            self.assertEqual(sc._recall_employed.call_count,int(ready))

    def test_cross_midnight_friend_time_range(self):
        start,end=parse_time_range('22:00-02:00','test')
        for hour,wanted in [(23,True),(1,True),(12,False),(2,False)]:
            self.assertEqual(in_time_range(datetime(2026,9,8,hour).time(),start,end),wanted)

    def test_all_queue_task_gates(self):
        runner=TaskQueueRunner.__new__(TaskQueueRunner)
        now=datetime(2026,9,8,12)
        for key in ('care','school','work','adventure','visit','pk','friend_care','hire_friend','svip'):
            task=_QueueTask(key)
            with self.subTest(task=key):
                self.assertTrue(runner._eligible(task,now))
                task.cfg.enabled=False
                self.assertFalse(runner._eligible(task,now))
                task.cfg.enabled=True; task.next_at=now+timedelta(seconds=60)
                self.assertFalse(runner._eligible(task,now))
                # 没有每日时间点了：daily 配置不再"打开执行窗口"，dead 就是当天不可继续
                task.next_at=datetime.min
                task.cfg.trigger='daily'; task.cfg.daily_times=['11:00']; task.dead=True
                self.assertFalse(runner._eligible(task,now))

    def test_plan_drives_main_choice(self):
        """主任务组由计划表第一条未完成条目决定，与每日时间点无关。"""
        runner=TaskQueueRunner.__new__(TaskQueueRunner)
        runner._main_pending_scen=Mock(return_value=None)
        runner._main_order=list(MAIN_TASK_KEYS)
        tasks={key:_QueueTask(key) for key in ('school','work','hire_friend','adventure')}
        cfg=Config()
        cfg.plan=PlanConfig(enabled=True, items=[
            PlanItemConfig(activity='school', mode='count', count=2),
            PlanItemConfig(activity='work', mode='duration', hours=1.0),
        ])
        runner._last_cfg=cfg
        runner._plan_progress=Mock(return_value=({'school':0,'work':0,'adventure':0},
                                                 {'school':0,'work':0,'adventure':0}))
        self.assertEqual(runner._main_choice(tasks,{}),'school')
        # 学习做完 -> 打工条目：优先雇佣好友
        runner._plan_progress=Mock(return_value=({'school':2,'work':0,'adventure':0},
                                                 {'school':1200,'work':0,'adventure':0}))
        self.assertEqual(runner._main_choice(tasks,{}),'hire_friend')
        # 雇佣好友当天不可继续 -> 普通打工兜底
        tasks['hire_friend'].dead=True
        self.assertEqual(runner._main_choice(tasks,{}),'work')
        # 全部条目完成 -> 不调度主任务
        runner._plan_progress=Mock(return_value=({'school':2,'work':3,'adventure':0},
                                                 {'school':1200,'work':3600,'adventure':0}))
        self.assertIsNone(runner._main_choice(tasks,{}))

    def test_plan_disabled_stops_main_tasks(self):
        """自动控制停用（plan.enabled=False）：不按计划执行主任务。"""
        runner=TaskQueueRunner.__new__(TaskQueueRunner)
        runner._main_pending_scen=Mock(return_value=None)
        runner._main_order=list(MAIN_TASK_KEYS)
        tasks={key:_QueueTask(key) for key in ('school','work','hire_friend','adventure')}
        cfg=Config(); cfg.plan=PlanConfig(enabled=False, items=[
            PlanItemConfig(activity='school', mode='count', count=2)])
        runner._last_cfg=cfg
        runner._plan_progress=Mock(return_value=({'school':0},{'school':0}))
        self.assertIsNone(runner._main_choice(tasks,{}))

    def test_moneybag_switch_and_first_check(self):
        """福袋开关：关掉就不巡检；开着时启动第一次调度立刻检查一次。"""
        runner=TaskQueueRunner.__new__(TaskQueueRunner)
        runner.school=Mock()
        runner._last_cfg=NS(runner=NS(moneybag_enabled=False,
                                      moneybag_interval_seconds=300))
        self.assertFalse(runner._try_moneybags())

        runner._last_cfg=NS(runner=NS(moneybag_enabled=True,
                                      moneybag_interval_seconds=300))
        runner._moneybag_next=0.0  # __init__ 的初值：启动后第一次就检查
        with patch('src.moneybag.MoneyBagCollector') as collector:
            collector.return_value.run.return_value=True
            collector.return_value.coins=0
            self.assertTrue(runner._try_moneybags())
        collector.return_value.run.assert_called_once()
        self.assertGreater(runner._moneybag_next,0)

    def test_plan_main_task_runs_before_side_tasks(self):
        """计划表优先：主任务可跑时，踩踩/PK 等支线不能插到前面。"""
        runner=TaskQueueRunner.__new__(TaskQueueRunner)
        runner.employed_due=Mock(return_value=False)
        runner._pending_finish_first=Mock(return_value=None)
        runner._eligible=Mock(return_value=True)
        runner.care_due=Mock(return_value=False)
        runner._main_choice=Mock(return_value='school')
        runner._main_pending_scen=Mock(return_value=None)
        runner._try_moneybags=Mock(return_value=False)
        runner._task_due=Mock(return_value=True)
        runner._execute=Mock()
        tasks={k:NS(name=k) for k in ('care','school','visit')}
        self.assertTrue(runner._run_first_due(tasks,['care','school','visit']))
        self.assertIs(runner._execute.call_args[0][0], tasks['school'])

    def test_side_task_runs_when_plan_item_not_ready(self):
        """计划条目跑不了（进行中/退避）时才轮到 tasks.order 里的支线。"""
        runner=TaskQueueRunner.__new__(TaskQueueRunner)
        runner.employed_due=Mock(return_value=False)
        runner._pending_finish_first=Mock(return_value=None)
        runner._eligible=Mock(return_value=True)
        runner.care_due=Mock(return_value=False)
        runner._main_choice=Mock(return_value=None)
        runner._main_pending_scen=Mock(return_value=None)
        runner._try_moneybags=Mock(return_value=False)
        runner._task_due=Mock(side_effect=lambda k,t,c: k=='visit')
        runner._execute=Mock()
        tasks={k:NS(name=k) for k in ('care','school','visit')}
        self.assertTrue(runner._run_first_due(tasks,['care','school','visit']))
        self.assertIs(runner._execute.call_args[0][0], tasks['visit'])

    def test_care_runs_before_plan_task(self):
        """护理是活动前置：低于阈值时先喂食/洗澡，再开计划主任务。"""
        runner=TaskQueueRunner.__new__(TaskQueueRunner)
        runner.employed_due=Mock(return_value=False)
        runner._pending_finish_first=Mock(return_value=None)
        runner._eligible=Mock(return_value=True)
        runner.care_due=Mock(return_value=True)
        runner._main_choice=Mock(return_value='school')
        runner._main_pending_scen=Mock(return_value=None)
        runner._try_moneybags=Mock(return_value=False)
        runner._task_due=Mock(return_value=True)
        runner._execute=Mock()
        tasks={k:NS(name=k) for k in ('care','school')}
        self.assertTrue(runner._run_first_due(tasks,['care','school']))
        self.assertIs(runner._execute.call_args[0][0], tasks['care'])

    def test_recovery_retry_stops_after_success(self):
        r=Runner.__new__(Runner)
        r._run_round=Mock(side_effect=[RuntimeError('test'),True])
        r._capture_failure_image=Mock(); r.recover=Mock()
        sc=Mock()
        self.assertTrue(r.run_one(sc,'打工'))
        r.recover.assert_not_called()

    def test_recovery_fatal_and_side_task_separation(self):
        for fatal in (False,True):
            r=Runner.__new__(Runner)
            r._run_round=Mock(side_effect=RuntimeError('test failure'))
            r._capture_failure_image=Mock(); r.recover=Mock(return_value=True)
            r._alert_and_exit=Mock(side_effect=SystemExit('test alert'))
            with self.assertRaises(SystemExit if fatal else ScenarioFailed):
                r.run_one(Mock(),'打工' if fatal else '踩踩',fatal=fatal)
            self.assertEqual(r._run_round.call_count,3)
            self.assertEqual(r._alert_and_exit.call_count,int(fatal))

    def test_pending_activity_blocks_new_main_task(self):
        r=TaskQueueRunner.__new__(TaskQueueRunner)
        pending=NS(pending={'until':datetime.now()+timedelta(minutes=10)},finish_pending=Mock())
        r._main_pending_scen=Mock(return_value=pending)
        self.assertIsNone(r._main_choice({},{}))
        pending.finish_pending.assert_not_called()

    def test_failure_backoff_is_honoured(self):
        r=TaskQueueRunner.__new__(TaskQueueRunner); t=_QueueTask('work')
        now=datetime(2026,9,8,12)
        t.cfg.failure_interval=1800
        r._fail_task(t,now,'injected test failure')
        self.assertFalse(r._eligible(t,now+timedelta(seconds=1799)))
        self.assertTrue(r._eligible(t,now+timedelta(seconds=1800)))

    def test_update_version_and_network_failure(self):
        from src.update_checker import _is_remote_newer, check_github_latest_release
        from urllib.error import URLError
        self.assertTrue(_is_remote_newer('0.7.0-local.2','v0.8.0'))
        self.assertFalse(_is_remote_newer('0.7.0-local.2','v0.7.0'))
        with patch('src.update_checker.urlopen',side_effect=URLError('offline test')):
            self.assertFalse(check_github_latest_release('owner/repo','0.7.0').ok)

    def test_coin_units_and_rightmost_coin(self):
        from src.coins import parse_coin, read_coins
        for token,expected in [('6.6k',6600),('1.2万',12000),('800',800),('错误',None)]:
            self.assertEqual(parse_coin(token),expected)
        with patch('src.coins.ocr_fullscreen',return_value=[('121',350,200,1),('6.6k',500,200,1),('23:40',100,20,1)]):
            self.assertEqual(read_coins(np.zeros((1280,720,3),dtype=np.uint8)),6600)

    def test_progress_rollover_preserves_history_and_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'work.json'
            store.write_raw(p,{'date':'2020-01-01','learned':3,'work_secs':100,
                               'duration':'10分钟','history':{'2019-12-31':2}})
            self.assertEqual(store.increment_daily(p),1)
            data=store.read_raw(p)
            self.assertEqual(data['duration'],'10分钟')
            self.assertEqual(data['history']['2020-01-01'],3)
            self.assertEqual(data['history']['2019-12-31'],2)
            self.assertNotIn('work_secs',data)

    def test_progress_corruption_does_not_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'broken.json'; p.write_text('{')
            self.assertEqual(store.read_raw(p),{})
            self.assertEqual(store.increment_daily(p),1)

    def test_config_validation_and_load(self):
        for location in WORK_LOCATIONS:
            self.assertEqual(validate_field('work.location',location),(True,location))
        self.assertFalse(validate_field('work.location','不存在')[0])
        self.assertIn(load_config().runner.engine,('task_queue','legacy'))

    def test_gui_all_pages_construct_and_switch(self):
        os.environ['QT_QPA_PLATFORM']='offscreen'
        import main
        from PyQt6.QtTest import QTest
        app=main.QApplication.instance() or main.QApplication([])
        # GUI 布局测试不访问真实模拟器管理器；设备连接另做真机测试。
        with patch.object(main.MainWindow,'_start_all'), patch.object(main.MainWindow,'_start_update_check'), patch.object(main.MainWindow,'_fill_devices'):
            window=main.MainWindow()
            self.assertFalse(any('emulator' in key for key in window._setting_widgets))
            # 主页/计划/统计/设置 4 页：原「任务」页已并入计划页（2026-10-20 重构）
            self.assertEqual(window.stackedWidget.count(),4)
            for i in range(window.stackedWidget.count()):
                page=window.stackedWidget.widget(i)
                self.assertTrue(page.objectName())
                window.stackedWidget.setCurrentIndex(i)
                QTest.qWait(600)  # Fluent 页面切换带动画，等待完成再检查。
                self.assertEqual(window.stackedWidget.currentIndex(),i)
            window.close()

    def test_notifications_failure_is_contained(self):
        from src.notify import send_alert
        with patch('src.notify.load_config',return_value=NS(notify=NS(win_toast=True,onepush_config='',pushplus_token=''))), patch('src.notify._send_windows_toast',side_effect=RuntimeError('test')):
            self.assertFalse(send_alert('本地测试，不对外发送'))

    def test_pushplus_alert_uses_configured_token(self):
        """配置了 pushplus_token 就走 pushplus 通道（不依赖 win_toast/onepush_config）。"""
        from src import notify
        token = 'T'*32
        calls=[]
        with patch('src.notify.load_config',return_value=NS(notify=NS(win_toast=False,onepush_config='',pushplus_token=token))), \
             patch('src.notify._onepush_notify',side_effect=lambda cfg,reason,image_path=None: calls.append(cfg) or True):
            self.assertTrue(notify.send_alert('本地测试，不对外发送'))
        self.assertEqual(calls,[{'provider':'pushplus','token':token}])

    def test_pushplus_token_falls_back_to_env(self):
        """配置留空时读环境变量 PUSHPLUS_TOKEN。"""
        from src import notify
        token = 'E'*32
        calls=[]
        with patch.dict(os.environ,{'PUSHPLUS_TOKEN':token}), \
             patch('src.notify.load_config',return_value=NS(notify=NS(win_toast=False,onepush_config='',pushplus_token=''))), \
             patch('src.notify._onepush_notify',side_effect=lambda cfg,reason,image_path=None: calls.append(cfg) or True):
            self.assertTrue(notify.send_alert('本地测试，不对外发送'))
        self.assertEqual(calls,[{'provider':'pushplus','token':token}])

    def test_pushplus_not_sent_twice_when_onepush_already_pushplus(self):
        """onepush_config 已经是 pushplus 时不再额外推一条，避免一次告警收两条。"""
        from src import notify
        calls=[]
        with patch('src.notify.load_config',return_value=NS(notify=NS(win_toast=False,onepush_config='{provider: pushplus, token: abc}',pushplus_token='T'*32))), \
             patch('src.notify._onepush_notify',side_effect=lambda cfg,reason,image_path=None: calls.append(cfg) or True):
            self.assertTrue(notify.send_alert('本地测试，不对外发送'))
        self.assertEqual(calls,[{'provider':'pushplus','token':'abc'}])

    def test_pushplus_failure_log_does_not_leak_token(self):
        """推送异常文本里万一带上 token，进日志前必须抹掉。"""
        from src import notify
        token = 'S'*32
        logs=[]
        with patch('src.notify.log',side_effect=lambda msg: logs.append(str(msg))), \
             patch('onepush.get_notifier',side_effect=RuntimeError(f'boom {token}')):
            self.assertFalse(notify._onepush_notify({'provider':'pushplus','token':token},'告警'))
        self.assertTrue(logs)
        self.assertFalse(any(token in m for m in logs), logs)

    def test_pushplus_business_code_failure_is_reported(self):
        """PushPlus 失败也回 HTTP 200，必须按响应体 code 判定，不能报"成功"。"""
        from src import notify
        logs=[]
        response=NS(status_code=200,json=lambda: {'code':903,'msg':'token无效'})
        notifier=NS(notify=lambda **kwargs: response)
        with patch('src.notify.log',side_effect=lambda msg: logs.append(str(msg))), \
             patch('onepush.get_notifier',return_value=notifier):
            self.assertFalse(notify._onepush_notify(
                {'provider':'pushplus','token':'X'*32},'告警'))
        self.assertTrue(any('code=903' in m for m in logs), logs)
        self.assertFalse(any('推送成功' in m for m in logs), logs)

    def test_main_page_rejects_care_panel_overlay(self):
        """洗澡页误判回归：护理面板盖在主页上时不算可操作的主页。

        面板是浮层，底下主页的"金币胶囊"节点仍在控件树里且坐标不变，
        只看金币胶囊会误判，随后在面板上点出门/按键直到超时。
        """
        from src.scenario import DeviceScenario
        dev = Mock()
        dev.current_package.return_value = 'com.tencent.mobileqq'
        sc = DeviceScenario(dev=dev)
        with patch.object(sc, 'see', side_effect=lambda n, screen=None, source=None:
                          (540, 240, 1.0) if n == 'main_sign' else None):
            self.assertIsNotNone(sc._on_main_page())
        for overlay in ('shower_10', 'feed_10', 'buy_soap', 'exchange_food'):
            with patch.object(sc, 'see', side_effect=lambda n, screen=None, source=None:
                              (540, 240, 1.0) if n in ('main_sign', overlay) else None):
                self.assertIsNone(sc._on_main_page(), overlay)

    def test_home_busy_parses_stacked_label_and_timer(self):
        """真机版面：'打工' 与 '00:27:03' 是上下两块，纵坐标差 67px 也要认出来。"""
        from src.ocr import parse_home_busy
        results=[('打工',600,2460,1.0),('00:27:03',600,2527,1.0)]
        self.assertEqual(parse_home_busy(results,2670)[:2],('work',1623))
        # 上半屏的同名文字不参与（避免把任务列表里的"打工"当成进度条）
        self.assertIsNone(parse_home_busy([('打工',600,400,1.0),('00:27:03',600,460,1.0)],2670))

    def test_leave_home_detects_busy_after_click_has_no_effect(self):
        """忙碌主页仍能匹配到"出门"且点了没反应：不能连点三次告警退出，要按忙碌态处理。"""
        from src.scenario import DeviceScenario
        dev=Mock(); dev.current_package.return_value='com.tencent.mobileqq'
        sc=DeviceScenario(dev=dev); sc.defer_wait=True
        screen=Mock(); screen.shape=(2670,1200,3)
        sc.snapshot=Mock(return_value=(screen,'ocr'))
        sc.see=Mock(return_value=(600,2500,1.0))
        sc.click=Mock()
        sc._on_main_page=Mock(return_value=(540,240,1.0))  # 点完还停在主页面
        sc.detect_home_busy=Mock(return_value=('work',1234))
        self.assertEqual(sc.leave_home(),'work')
        sc.click.assert_called_once()
        self.assertIsNotNone(sc.pending)
        self.assertGreater(sc.pending['until'],__import__('datetime').datetime.now())

    def test_leave_home_has_no_blind_coordinate_fallback(self):
        """出门找不到时必须报错走恢复，不能按参考坐标盲点（真机连点 3 次的根因）。"""
        from src.locators import LOCATORS
        self.assertNotIn('rel', LOCATORS['leave_home'])

    def test_resumed_package_parses_top_activity(self):
        """前台包名以系统 resumed activity 为准（u2 的 app_current 在 MIUI 上不准）。"""
        from src.adb.device import Device
        dev = Device('adb')
        ok = NS(returncode=0, stdout=(
            b'topResumedActivity=ActivityRecord{55059907 u0 '
            b'com.tencent.mobileqq/com.tencent.robot.adelie.AdelieFragmentActivity t26079}\n'))
        with patch.object(dev, '_run', return_value=ok):
            self.assertEqual(dev.resumed_package(), 'com.tencent.mobileqq')
        with patch.object(dev, '_run', return_value=NS(returncode=1, stdout=b'')):
            self.assertEqual(dev.resumed_package(), '')

    def test_foreground_guard_blocks_other_apps(self):
        """用户切到别的 App 时必须立刻停手，不能盲点/盲拖。"""
        from src.scenario import DeviceScenario
        dev = Mock()
        dev.current_package.return_value = 'com.eg.android.AlipayGphone'
        sc = DeviceScenario(dev=dev)
        with self.assertRaises(RuntimeError):
            sc.ensure_game_foreground()
        dev.current_package.return_value = 'com.tencent.mobileqq'
        sc.ensure_game_foreground()  # 前台是 QQ 不抛
        dev.current_package.return_value = ''
        sc.ensure_game_foreground()  # 取不到包名不阻断主流程

    def test_adventure_duration_defaults_to_short(self):
        """冒险默认选 45 秒的"附近走走"，不是 2 小时的"诗和远方"。"""
        from src.config import AdventureConfig
        from src import settings as settings_io
        self.assertEqual(AdventureConfig().duration, '45秒')
        self.assertEqual(settings_io.DEFAULTS['adventure.duration'], '45秒')
        ok, value = settings_io.validate_field('adventure.duration', '3小时')
        self.assertFalse(ok)
        self.assertEqual(value, '45秒')
        ok, value = settings_io.validate_field('adventure.duration', '2小时')
        self.assertTrue(ok)

    def test_select_duration_clicks_configured_option(self):
        """准备页按配置点选时长，并用"简介"文字确认真的选中了。"""
        from scenarios.adventure import AdventureScenario
        sc = AdventureScenario(dev=Mock())
        sc.duration = '45秒'
        state = {'selected': False, 'clicks': []}

        def fake_see(name, screen=None, source=None):
            if name in ('adventure_start', 'adventure_short'):
                return (100, 200, 1.0)
            if name == 'adventure_short_desc':
                return (1, 1, 1.0) if state['selected'] else None
            return None

        def fake_click(x, y):
            state['clicks'].append((x, y))
            state['selected'] = True  # 点完简介才变成短时

        with patch.object(sc, 'see', side_effect=fake_see),              patch.object(sc, 'click', side_effect=fake_click):
            sc.select_duration()
        self.assertEqual(state['clicks'], [(100, 200)], '未选中时应点一次选项')
        # 已经是目标时长：连跑时不该重复点
        with patch.object(sc, 'see', side_effect=fake_see),              patch.object(sc, 'click', side_effect=fake_click):
            sc.select_duration()
        self.assertEqual(len(state['clicks']), 1, '已选中时不应重复点击')
        # 点了但简介没变 = 没选中，必须报错（否则真的会跑 2 小时）
        with patch.object(sc, 'see', side_effect=lambda n, screen=None, source=None:
                          (100, 200, 1.0) if n in ('adventure_start', 'adventure_short') else None),              patch.object(sc, 'click'):
            with self.assertRaises(RuntimeError):
                sc.select_duration()

    def test_adventure_start_accepts_new_button_text(self):
        """2026-10 新版冒险按钮文案是"出发"，旧的"开始"仍要兼容。"""
        from src.locators import LOCATORS
        entry = LOCATORS['adventure_start']
        self.assertIn('//*[@content-desc="出发"]', entry['xpath'])
        self.assertIn('//*[@content-desc="开始"]', entry['xpath'])
        self.assertIn('出发', entry['ocr'])

    def test_svip_entry_matches_left_icon_column(self):
        """2026-10 新版图标列在左侧：模板匹配必须能找到（旧 ROI 只在右侧）。"""
        import cv2
        from scenarios.svip import find_entry_icon
        from src.config import resource_path
        tpl = cv2.imdecode(
            np.fromfile(resource_path('resources/svip-entry.png'), dtype=np.uint8),
            cv2.IMREAD_GRAYSCALE)
        screen = np.full((1200, 1080, 3), 240, dtype=np.uint8)
        size = 70
        tx, ty = 90, 850  # 左侧图标列 ROI（x 30~240, y 500~1300）
        screen[ty:ty + size, tx:tx + size] = cv2.cvtColor(
            cv2.resize(tpl, (size, size)), cv2.COLOR_GRAY2RGB)
        hit = find_entry_icon(screen)
        self.assertIsNotNone(hit)
        cx, cy, score = hit
        self.assertGreater(score, 0.85)
        self.assertLess(abs(cx - (tx + size // 2)), 10)
        self.assertLess(abs(cy - (ty + size // 2)), 10)

    def test_inventory_ocr_tolerates_short_noise(self):
        """OCR 把 '98' 认成 'L8' 时不能当成"库存为 0"去误购。"""
        from scenarios.care import nearest_item_count
        self.assertEqual(nearest_item_count([('L8', 100, 100, 1)], 100, 100), 8)
        self.assertEqual(nearest_item_count([('x12', 100, 100, 1)], 100, 100), 12)
        self.assertIsNone(nearest_item_count([('美', 100, 100, 1)], 100, 100))

    def test_migrate_tasks_order_skips_missing_config(self):
        """首次启动 config.yaml 还不存在时，迁移应静默跳过而不是报失败。"""
        from src import settings
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / 'config.yaml'
            with patch.object(settings, 'CONFIG_FILE', missing):
                settings.migrate_tasks_order()
            self.assertFalse(missing.exists())

    def test_scrcpy_serial_falls_back_to_first_online_device(self):
        """多设备在线且配置留空时，scrcpy 必须带上 -s，否则会直接退出。"""
        import main
        with patch.object(main, 'load_config',
                          return_value=NS(adb=NS(path='adb', device_serial=''))), \
             patch.object(main, 'find_adb', return_value='adb'), \
             patch.object(main.Device, 'ensure_connected', return_value='3a4e7af5'):
            self.assertEqual(main._resolve_serial(), '3a4e7af5')
        with patch.object(main, 'load_config',
                          return_value=NS(adb=NS(path='adb', device_serial='ABC123'))):
            self.assertEqual(main._resolve_serial(), 'ABC123')

    def test_update_check_falls_back_to_release_page(self):
        """GitHub API 403/限流时改用公开 Release 页面拿最新版本。"""
        from urllib.error import HTTPError
        from src import update_checker as uc

        class FakeResp:
            def __init__(self, url):
                self._url = url

            def geturl(self):
                return self._url

            def read(self):
                return b''

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=0):
            url = getattr(req, 'full_url', str(req))
            if 'api.github.com' in url:
                raise HTTPError(url, 403, 'rate limited', {}, None)
            return FakeResp('https://github.com/hu181b/qq-pet-copilot/releases/tag/v1.41')

        with patch.object(uc, 'urlopen', side_effect=fake_urlopen):
            result = uc.check_github_latest_release('hu181b/qq-pet-copilot', '1.4')
        self.assertTrue(result.ok)
        self.assertTrue(result.has_update)
        self.assertEqual(result.latest_tag, 'v1.41')


if __name__=='__main__':
    unittest.main(verbosity=2)
