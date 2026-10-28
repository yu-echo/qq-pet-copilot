"""好友护理场景（与经验日常合并）：访问第一个好友，检测到可加经验就一键护理。

流程（好友导航复用 visit.py 的 goto_first_friend）：
1. 点击 好友（visit_friends）-> 访问（visit）进入第一个好友宠物页
   （不再检测好友名称，自动选第一个好友）
2. 检测照顾区域（one_click_care 的父级 care_region）：OCR 含 "exp/经验"
   就点 one_click_care 一键护理（含"支付并护理"确认），加经验；
   区域无 exp 则视为当日经验日常已完成并持久化
3. 结束：关闭好友页面（点 back）回主页面。每次调度只做一次巡检，
   下次调度间隔由 friend_care.interval_seconds 控制（场景内不等待/切换好友）

配置（config.yaml 的 friend_care 段）：enabled 开关 / interval_seconds 调度间隔（秒）。
（旧字段 time_range / friend_name / method 已废弃，仅保留兼容旧配置）

运行：python scenarios/friend_care.py            （Ctrl+C 停止）
"""

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.locators import see_bounds
from src.ocr import ocr_texts
from src.progress import log, save_exp_daily
from src.scenario import CLICK_INTERVAL, DeviceScenario
from scenarios.care import ONE_CLICK_PAY_RETRIES
from scenarios.visit import VisitScenario

FRIEND_CARE_RETRIES = 2   # 进好友家/护理偶发卡顿时回主页面重进好友家再试的次数


def parse_time_range(value: str, name: str = 'friend_care.time_range') -> tuple:
    """解析时间段 'HH:MM-HH:MM' 为 (开始, 结束) time。"""
    try:
        start_s, end_s = str(value).split('-', 1)
        start = datetime.strptime(start_s.strip(), '%H:%M').time()
        end = datetime.strptime(end_s.strip(), '%H:%M').time()
    except ValueError:
        raise ValueError(
            f'config.yaml 中 {name} 格式无效: {value!r}，应为 HH:MM-HH:MM') from None
    return start, end


def in_time_range(now, start, end) -> bool:
    """now 是否在 [start, end) 时间段内（end <= start 视为跨零点，如 22:00-02:00）。"""
    if end > start:
        return start <= now < end
    return now >= start or now < end


class FriendCareScenario(VisitScenario):
    def __init__(self, dev=None):
        DeviceScenario.__init__(self, dev)  # 跳过 VisitScenario 的踩踩字段/日志
        fc = self.cfg.friend_care
        self.last_care_at: datetime | None = None  # 上次巡检完成时间（调度间隔用）
        log(f'好友护理（经验日常）: {"启用" if fc.enabled else "未启用"}，'
            f'调度间隔: {fc.interval_seconds}秒')

    # ---- 经验日常（一键护理加经验） ----

    def _try_exp_daily(self) -> bool:
        """当前好友宠物页尝试经验日常：有 one_click_care 时 OCR 其父级"照顾区域"，
        含"exp/经验"就点 one_click_care（加经验）；区域无 exp 则视为当日经验日常
        完成并持久化。返回是否执行了一键护理（加经验）。"""
        # 一次控件树快照复用：one_click_care 和父级 care_region 同一次 dump 里查
        source = self.dev.hierarchy()
        care = self.see('one_click_care', source=source)
        if not care:
            return False  # 没有照顾按钮，跳过（不标记完成）
        bounds = see_bounds(self.dev, 'care_region', source=source)
        if not bounds:
            return False
        x1, y1, x2, y2 = bounds
        results = ocr_texts(self.screen()[y1:y2, x1:x2])
        log('照顾区域 OCR: '
            + (', '.join(f'{t!r}@({x},{y})' for t, x, y, _ in results) or '无'))
        if any(('exp' in (t or '').lower()) or ('经验' in (t or '')) for t, *_ in results):
            log(f'照顾区域含 exp 经验值，点击一键护理 ({care[0]}, {care[1]})')
            self.click(care[0], care[1])
            # 支付确认弹窗可能比护理按钮点击晚一拍出现，短等几次再判断（同 care.py）
            for attempt in range(1, ONE_CLICK_PAY_RETRIES + 1):
                pay = self.see('one_click_pay')
                if pay:
                    log(f'检测到"支付并护理"，点击确认 ({pay[0]}, {pay[1]})')
                    self.click(pay[0], pay[1])
                    time.sleep(CLICK_INTERVAL)
                    break
                if attempt < ONE_CLICK_PAY_RETRIES:
                    time.sleep(CLICK_INTERVAL)
            return True
        log('照顾区域无 exp，经验日常已完成')
        save_exp_daily(True)
        return False

    # ---- 入口 ----

    def run(self, max_times: int | None = None, max_rounds: int = 0) -> bool:
        """回主页面 -> 进第一个好友家 -> 检测到可加经验就一键护理 -> 结束回主页面。

        每次调度只做一次巡检（调度间隔 friend_care.interval_seconds 由执行器的
        friend_care_due() 控制），场景内不再等待/切换好友。
        max_times / max_rounds 参数仅为与其他场景签名一致（预留）。
        返回 True 表示完成了一次巡检（无论是否执行护理动作）——返回 False 会被
        任务队列标记当天不可继续，"无需护理/经验日常已完成"也必须返回 True
        以便间隔后复查。
        """
        fc = self.cfg.friend_care
        if not fc.enabled:
            log('好友护理未启用，跳过')
            return False
        log('好友护理开始（经验日常：检测到可加经验就一键护理）')
        # 好友家有概率卡顿（护理面板打不开、页面卡死）：失败后回主页面
        # 重新进第一个好友家再试一次；仍失败抛给调度器走恢复链路
        cared = False
        for attempt in range(1, FRIEND_CARE_RETRIES + 1):
            try:
                self.ensure_main_page()
                self.goto_first_friend()
                cared = self._try_exp_daily()
                break
            except Exception as e:
                if attempt >= FRIEND_CARE_RETRIES:
                    raise
                log(f'好友护理第 {attempt} 次尝试失败: {e}，回主页面重新进好友家再试')
                time.sleep(CLICK_INTERVAL)
        log('好友护理巡检完成' + ('，本次一键护理加经验' if cared else '，经验日常已完成或无需护理'))
        self.close()  # 先点 back 收掉好友相关页面，再确认回主页面
        self.ensure_main_page()
        self.last_care_at = datetime.now()
        return True


if __name__ == '__main__':
    try:
        FriendCareScenario().run()
    except KeyboardInterrupt:
        log('手动停止')
