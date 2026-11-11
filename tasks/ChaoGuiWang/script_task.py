# This Python file uses the following encoding: utf-8
"""超鬼王 · 祸主降临。

流程（对应需求中的九个步骤）：
    1. 庭院点击最右侧的超鬼王活动图标
    2. 点击「祸主降临」文字
    3. 等待「自己发现」区域的鬼面图标出现（每 5 秒检查一次，最多 40 秒）
    4. 读取右上角疲劳度，超过阈值则推送并停止
    5. 点击右下角「召唤鬼王」
    6. 点击右下角「挑战」
    7. 点击右下角「准备」，等待鬼王打完（最多 200 秒）
    8. 结算界面点击用户标注的三个安全区域之一
    9. 游戏自动回到第 3 步界面，循环直至击杀数量达标

全局要求：
    a. 任务执行前切换御魂预设
    b. 可设置击杀数量；完成一轮（第3步到第8步）计一只，达标后推送并结束

消息推送统一走框架通知 self.config.notifier.push()，
在「脚本设置 → 错误」里开启推送并配置 provider: pushplus 即为 PushPlus 推送。
"""
import random
from time import sleep

from module.base.timer import Timer
from module.exception import TaskEnd
from module.logger import logger

from tasks.ChaoGuiWang.assets import ChaoGuiWangAssets
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
from tasks.Component.RightActivity.right_activity import RightActivity
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.GameUi.page import page_main, page_shikigami_records


class ScriptTask(RightActivity, SwitchSoul, GeneralBattleAssets, ChaoGuiWangAssets):
    """超鬼王（祸主降临）任务。"""

    # ------------------------------------------------------------------
    # 推送
    # ------------------------------------------------------------------
    def push(self, title: str, content: str) -> None:
        """统一走框架消息推送。

        notify_config 形如：
            provider: pushplus
            token: 你的pushplus_token
        未开启推送时框架内部会安全地直接返回，不影响任务流程。
        """
        try:
            self.config.notifier.push(title=title, content=content)
        except Exception as e:
            logger.exception(f'消息推送失败: {e}')

    # ------------------------------------------------------------------
    # 全局要求 a：御魂预设
    # ------------------------------------------------------------------
    def switch_soul_before_run(self, soul_conf) -> None:
        """执行任务前切换御魂预设，支持按编号与按名称两种方式。"""
        if soul_conf.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(soul_conf.switch_group_team)
        if soul_conf.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(soul_conf.group_name, soul_conf.team_name)
        self.goto_page(page_main)

    # ------------------------------------------------------------------
    # 步骤 1、2：进入活动
    # ------------------------------------------------------------------
    def open_huozhu(self) -> bool:
        """点击「祸主降临」，直到第 3 步的鬼面图标出现。"""
        timer = Timer(30).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_CGW_DEMON_ICON):
                logger.info('已进入祸主降临界面')
                return True
            if self.appear_then_click(self.I_CGW_HUOZHU,
                                      action=self.C_CGW_HUOZHU_CLICK, interval=2):
                continue
            if self.ui_reward_appear_click():
                continue
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1.5):
                continue
            if timer.reached():
                logger.warning('进入祸主降临界面超时')
                return False
            sleep(0.5)

    # ------------------------------------------------------------------
    # 步骤 3：等待鬼面图标
    # ------------------------------------------------------------------
    def wait_demon_icon(self, cfg) -> bool:
        """只认「自己发现」区域红框内的鬼面图标；未出现则间隔等待，超时返回 False。"""
        timer = Timer(cfg.wait_icon_timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_CGW_DEMON_ICON):
                return True
            if timer.reached():
                logger.warning(f'等待鬼面图标超时（{cfg.wait_icon_timeout} 秒）')
                return False
            sleep(cfg.wait_icon_interval)

    # ------------------------------------------------------------------
    # 步骤 4：疲劳度
    # ------------------------------------------------------------------
    def get_fatigue(self) -> int | None:
        """读取右上角「xx/100」疲劳度；DigitCounter 返回 (当前, 剩余, 上限)。"""
        for _ in range(3):
            self.screenshot()
            current, _, total = self.O_CGW_FATIGUE.ocr(self.device.image)
            if total > 0:
                return int(current)
            sleep(1)
        logger.warning('疲劳度识别失败')
        return None

    # ------------------------------------------------------------------
    # 步骤 5~8：召唤、挑战、准备、结算
    # ------------------------------------------------------------------
    def settle_click_areas(self) -> list:
        """用户绿框标注的三处结算可点击区域。"""
        return [self.C_CGW_SETTLE_LEFT, self.C_CGW_SETTLE_RIGHT, self.C_CGW_SETTLE_BOTTOM]

    def click_stage(self, image, action=None, timeout: int = 20) -> bool:
        """等待某个按钮出现并点击一次，超时返回 False。"""
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if action is None:
                clicked = self.appear_then_click(image, interval=1.2)
            else:
                clicked = self.appear_then_click(image, action=action, interval=1.2)
            if clicked:
                logger.info(f'点击 {image.name}')
                return True
            if self.ui_reward_appear_click():
                continue
            if timer.reached():
                logger.warning(f'等待 {image.name} 超时（{timeout} 秒）')
                return False
            sleep(0.3)

    def battle_once(self, cfg) -> bool:
        """完成一次击杀：召唤 → 挑战 → 准备 → 等待结算 → 点击绿框返回主界面。"""
        # 步骤 5：召唤鬼王
        if not self.click_stage(self.I_CGW_SUMMON, self.C_CGW_BOTTOM_BUTTON, timeout=20):
            return False
        # 步骤 6：挑战
        if not self.click_stage(self.I_CGW_CHALLENGE, self.C_CGW_BOTTOM_BUTTON, timeout=20):
            return False
        # 步骤 7：准备（复用通用战斗组件的高亮准备按钮）
        if not self.click_stage(self.I_PREPARE_HIGHLIGHT, timeout=30):
            return False

        # 长战斗需登记白名单，避免被设备的卡死检测误判
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        logger.info('已点击准备，等待鬼王战斗结束')
        timer = Timer(cfg.battle_timeout).start()
        settle_clicked = False
        while 1:
            self.screenshot()
            # 步骤 8：结算界面，点击绿框标注的任意一处
            if self.appear(self.I_CGW_SETTLE) or self.appear(self.I_WIN):
                settle_clicked = True
                self.click(random.choice(self.settle_click_areas()), interval=1.2)
                continue
            if self.ui_reward_appear_click():
                continue
            # 步骤 9：点击结算后游戏自动回到第 3 步界面
            # 鬼面图标为主判定，召唤鬼王按钮作为主界面辅助判定（避免新鬼王卡片遮挡时卡住）
            if settle_clicked and (self.appear(self.I_CGW_DEMON_ICON)
                                  or self.appear(self.I_CGW_SUMMON)):
                logger.info('已回到祸主降临界面，本轮击杀完成')
                return True
            if timer.reached():
                logger.warning(f'等待鬼王战斗结束超时（{cfg.battle_timeout} 秒）')
                return False
            sleep(0.3)

    def recover(self) -> None:
        """超时重试前尝试清掉残留的弹窗/结算界面，尽量回到第 3 步界面。"""
        timer = Timer(10).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_CGW_DEMON_ICON) or self.appear(self.I_CGW_SUMMON):
                return
            if self.appear(self.I_CGW_SETTLE) or self.appear(self.I_WIN):
                self.click(random.choice(self.settle_click_areas()), interval=1.2)
                continue
            if self.ui_reward_appear_click():
                continue
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1.5):
                continue
            if timer.reached():
                return
            sleep(0.4)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(self):
        conf = self.config.chao_gui_wang
        cfg = conf.chao_gui_wang_config
        killed = 0
        consecutive_timeout = 0
        stop_reason = ''
        final_pushed = False

        # 全局要求 a：执行任务前切换御魂预设
        self.switch_soul_before_run(conf.switch_soul_config)

        # 步骤 1、2：庭院 → 活动图标 → 祸主降临
        self.enter(self.I_CGW_ENTRY)
        if not self.open_huozhu():
            self.push('超鬼王·进入活动失败', '未能进入「祸主降临」界面，任务结束，请检查活动是否已开放。')
            self.set_next_run(task='ChaoGuiWang', success=False, finish=True)
            raise TaskEnd('ChaoGuiWang')

        # 全局要求 b：按设置的击杀数量循环
        while killed < cfg.kill_count:
            logger.hr(f'超鬼王 第 {killed + 1}/{cfg.kill_count} 只', 2)

            # 步骤 3：等待鬼面图标
            if not self.wait_demon_icon(cfg):
                self.push('超鬼王·等待图标超时',
                          f'等待「自己发现」鬼面图标超过 {cfg.wait_icon_timeout} 秒，'
                          f'已击杀 {killed}/{cfg.kill_count} 只，将重试下一轮。')
                consecutive_timeout += 1
                if consecutive_timeout >= cfg.max_consecutive_timeout:
                    stop_reason = '连续超时，任务终止'
                    break
                self.recover()
                continue

            # 步骤 4：疲劳度判定（严格「超过」阈值才停止）
            fatigue = self.get_fatigue()
            if fatigue is None:
                self.push('超鬼王·疲劳度识别失败',
                          f'右上角疲劳度连续识别失败，为避免超疲劳，任务停止。已击杀 {killed} 只。')
                stop_reason = '疲劳度识别失败'
                final_pushed = True
                break
            logger.info(f'当前疲劳度 {fatigue}/100，阈值 {cfg.fatigue_limit}')
            if fatigue > cfg.fatigue_limit:
                self.push('超鬼王·疲劳度已达上限',
                          f'当前疲劳度 {fatigue}/100，超过设定阈值 {cfg.fatigue_limit}，'
                          f'任务停止。已击杀 {killed}/{cfg.kill_count} 只。')
                stop_reason = '疲劳度超过阈值'
                final_pushed = True
                break

            # 步骤 5~8：完成一轮计一只
            if not self.battle_once(cfg):
                self.push('超鬼王·本轮超时',
                          f'召唤/挑战/准备/结算环节超时（战斗上限 {cfg.battle_timeout} 秒），'
                          f'已击杀 {killed}/{cfg.kill_count} 只，将重试下一轮。')
                consecutive_timeout += 1
                if consecutive_timeout >= cfg.max_consecutive_timeout:
                    stop_reason = '连续超时，任务终止'
                    break
                self.recover()
                continue

            killed += 1
            consecutive_timeout = 0
            logger.info(f'已击杀超鬼王 {killed}/{cfg.kill_count} 只')

        # 结束推送与调度
        if killed >= cfg.kill_count:
            self.push('超鬼王·任务完成',
                      f'已完成设定的击杀数量 {killed} 只超鬼王，任务结束。')
        elif not final_pushed and stop_reason:
            self.push('超鬼王·任务提前结束',
                      f'结束原因：{stop_reason}；已击杀 {killed}/{cfg.kill_count} 只。')

        logger.info(f'超鬼王任务结束，共击杀 {killed} 只')
        self.set_next_run(task='ChaoGuiWang', success=True, finish=True)
        raise TaskEnd('ChaoGuiWang')
