# -*- coding: utf-8 -*-
"""每日领取 QQ SVIP 会员礼包场景。

入口：主页宠物状态卡右上角的企鹅帽图标（"点击有礼"），点开是"QQ SVIP专属礼包"
弹窗，三种状态：
- 会员且今日未领：点"立即领取"领取，记进度后当天不再执行；
- 会员且今日已领：按钮是"明日再来"，记进度后当天不再执行；
- 非会员：按钮是"开通 SVIP"——关掉弹窗，并把 tasks.svip.enabled=false 写回
  config.yaml 自动关闭该任务（调度器 reload_config 每轮热加载，下一轮即生效）。

调度走任务队列：tasks.svip.trigger=daily + daily_times 每天到点执行一次；
"今天是否已领取"持久化在 runs/svip_progress.json（跨天自动失效）。
"""
from __future__ import annotations

import time

from src.progress import log, save_svip_claim, svip_claimed_today
from src.scenario import CLICK_INTERVAL, DeviceScenario

# 找入口/等弹窗的重试轮数（每轮间隔 CLICK_INTERVAL）
ENTRY_ATTEMPTS = 3
DIALOG_ATTEMPTS = 5
# 点"立即领取"后等奖励展示/弹窗刷新的轮数
CLAIM_SETTLE_ATTEMPTS = 5
# "开通 SVIP"复核等待（秒）：弹窗按钮区可能先渲染开通模板再刷新
OPEN_RECHECK_WAIT = 1.5
# 等弹窗按钮出现的轮数（标题已出但按钮区可能还在加载）
STATE_ATTEMPTS = 4


class SvipScenario(DeviceScenario):
    """每日领取 QQ SVIP 会员礼包；非会员自动关闭该任务。"""

    def run(self, max_times: int | None = None, max_rounds: int = 0) -> bool:
        """执行一次领取流程。

        返回 True = 本次领取成功（调度按 success_interval 排下次，但当天
        进度已标记，svip_due 会挡住重复执行）；返回 False = 当天无需/不可
        再执行（已领取或非会员已自动关任务），任务队列会把当天标记为结束。
        找不到入口/弹窗异常则抛错，交给调度器的失败退避处理。
        """
        if svip_claimed_today():
            log('SVIP礼包: 今天已领取过，跳过')
            return False
        log('SVIP礼包: 尝试领取每日会员礼包')
        self.ensure_main_page()
        try:
            return self._claim_once()
        finally:
            self.ensure_main_page()

    # ---- 主流程 ----

    def _claim_once(self) -> bool:
        hit = self._find_entry()
        if hit is None:
            raise RuntimeError('主页未找到 SVIP 礼包入口（点击有礼）')
        self.click(hit[0], hit[1])
        screen = self._wait_dialog()
        # 标题出了按钮区可能还在加载：轮询等按钮状态出现，避免误报"没识别到按钮"
        state = None
        for attempt in range(1, STATE_ATTEMPTS + 1):
            state = self._dialog_state(screen)
            if state is not None:
                break
            log(f'等待礼包弹窗按钮出现 ({attempt}/{STATE_ATTEMPTS})')
            time.sleep(CLICK_INTERVAL)
            screen = self.screen()
        if state == 'open':
            # 弹窗按钮区可能先渲染"开通 SVIP"模板再刷新成实际状态
            # （真机见过同一入口一次"开通 SVIP"、一次"明日再来"），复核一轮防误判，
            # 误判一次就会把任务误关。
            time.sleep(OPEN_RECHECK_WAIT)
            screen = self.screen()
            state = self._dialog_state(screen)

        if state == 'open':
            log('SVIP礼包弹窗按钮是"开通 SVIP"（复核后仍是）：当前账号不是 SVIP 会员，'
                '关闭弹窗并自动关闭 SVIP礼包 任务')
            self._close_dialog(screen)
            self._disable_task()
            return False

        if state == 'tomorrow':
            log('SVIP礼包弹窗按钮是"明日再来"：今天已领取过')
            save_svip_claim(True)
            self._close_dialog(screen)
            return False

        if state != 'claim':
            raise RuntimeError('SVIP 弹窗未识别到 领取/明日再来/开通SVIP 任一按钮')
        claim = self.see('svip_claim', screen)
        self.click(claim[0], claim[1])
        # 领取后可能有奖励展示，等弹窗变为"明日再来"或消失再收尾
        for _ in range(CLAIM_SETTLE_ATTEMPTS):
            time.sleep(CLICK_INTERVAL)
            screen = self.screen()
            if self.see('svip_tomorrow', screen) or not self.see('svip_dialog', screen):
                break
        save_svip_claim(True)
        log('SVIP礼包: 每日会员礼包领取成功')
        self._close_dialog(screen)
        return True

    # ---- 分步 ----

    def _dialog_state(self, screen) -> str | None:
        """识别弹窗当前按钮状态：'claim'（立即领取）/ 'tomorrow'（明日再来）/
        'open'（开通 SVIP）；都不匹配返回 None。顺序：先已领取再领取最后开通，
        避免 OCR 碎片互相误命中。"""
        if self.see('svip_tomorrow', screen):
            return 'tomorrow'
        if self.see('svip_claim', screen):
            return 'claim'
        if self.see('svip_open', screen):
            return 'open'
        return None

    def _find_entry(self) -> tuple[int, int, float] | None:
        """在主页找企鹅帽入口（"点击有礼"），重试 ENTRY_ATTEMPTS 轮。"""
        for attempt in range(1, ENTRY_ATTEMPTS + 1):
            screen = self.screen()
            hit = self.see('svip_entry', screen)
            if hit:
                return hit
            log(f'主页未找到 SVIP 礼包入口，等待重试 ({attempt}/{ENTRY_ATTEMPTS})')
            time.sleep(CLICK_INTERVAL)
        return None

    def _wait_dialog(self):
        """点入口后等礼包弹窗出现，返回弹窗页截图；超时抛错交失败退避。"""
        for attempt in range(1, DIALOG_ATTEMPTS + 1):
            time.sleep(CLICK_INTERVAL)
            screen = self.screen()
            if self.see('svip_dialog', screen):
                return screen
            log(f'等待 SVIP 礼包弹窗出现 ({attempt}/{DIALOG_ATTEMPTS})')
        raise RuntimeError('点击入口后未出现 SVIP 礼包弹窗')

    def _close_dialog(self, screen=None) -> None:
        """关掉礼包弹窗：优先点小关闭按钮（content-desc 关闭），否则系统返回键。"""
        try:
            source = self.dev.hierarchy()
            hit = self.see('svip_close', None, source)
        except Exception:
            hit = None
        if hit:
            self.click(hit[0], hit[1])
        else:
            log('SVIP礼包: 未找到关闭按钮，按系统返回键关弹窗')
            self.go_back()
        time.sleep(CLICK_INTERVAL)

    @staticmethod
    def _disable_task() -> None:
        """非会员：把 tasks.svip.enabled=false 写回 config.yaml（照 u2dev
        回退控制方案的范式）。调度器 reload_config 每轮热加载，下一轮即生效；
        GUI 任务表每秒刷新，开关会显示为关。"""
        try:
            from src.settings import load_raw, save_raw, set_value
            data = load_raw()
            set_value(data, 'tasks.svip.enabled', False)
            save_raw(data)
            log('已把 tasks.svip.enabled=false 写回 config.yaml'
                '（非会员不再调度该任务；之后开通了会员，在任务表把 SVIP礼包 '
                '开关重新打开即可）')
        except Exception as e:  # noqa: BLE001 - 写配置失败不阻断本次流程
            log(f'写回 config.yaml 失败（{e}），请手动把 SVIP礼包 任务开关关闭')
