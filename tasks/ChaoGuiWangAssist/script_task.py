# This Python file uses the following encoding: utf-8
"""超鬼王协战。

本任务复用「超鬼王（祸主降临）」的入口、星级识别、战斗与结算流程，
只把「打出鬼王」换成「把鬼王邀请给指定好友」或「协战别人的鬼王」。

公共前置（两种模式一致）：
    1. 庭院点击右侧活动列里的超鬼王图标
    2. 点击「祸主降临」
    3. 进入祸主降临主界面（「自己发现」「好友发现」两块区域可读）

两个模式的边界（严格区分，互不越界）：
    - 「集结」印章只会出现在「自己发现」的鬼王卡片上，别人分享的鬼王没有；
    - 邀请模式：只管自己的鬼王 → 召唤 → 集结 → 邀请给指定好友，不点挑战、不切御魂；
    - 协战模式：只管别人的鬼王 → 不召唤、不集结、不邀请、不打自己发现的鬼王。

--------------------------------------------------------------------
邀请模式：
    4. 等「自己发现」红框内的鬼面图标出现（已有自己的鬼王卡片则跳过召唤）
    5. 识别鬼王星级（圆形图标下方橙色勾玉个数），只处理勾选的星级；
       三个星级开关全不勾时任务启动即退出，识别到未勾选星级则推送并结束
    6. 不判疲劳、不点挑战：点「召唤鬼王」，再点卡片右上角的「集结」印章
    7. 分享弹窗里只找配置指定的那一位好友（好友页签 → 寮友页签），
       点中他再点「邀请」；两个页签都没有就关掉弹窗、不邀请任何人并结束任务
    8. 每 wait_interval 秒检查一次「自己发现」鬼面是否重新出现：
       重现 = 好友击杀成功 = 邀请成功一次；超过 wait_timeout 则推送提醒后继续等

协战模式：
    4. 只看「好友发现」区域：
       - 鬼面空位图标存在 → 没有可协战鬼王，每 wait_interval 秒重查，
         超过 wait_timeout 仍存在则推送「无协战鬼王」并结束
       - 鬼王卡片带「已公开」印章 → 按需求忽略，继续等待
       - 存在鬼王卡片且没有「已公开」印章 → 非公开鬼王：先点这张卡片把自己
         的选中目标切到好友的鬼王，再点挑战 → 准备 → 结算，
         出现一次胜利结算即协战成功一只

推送：只保留任务完成、任务完全失败、打完所选星级、无协战鬼王、邀请超时、
未找到指定好友、连续协战失败、任务结束汇总八类，各有独立开关，
并受 notify_enable 总开关控制；按需求，成功次数不在过程中逐条推送，
只在结束汇总里给出。

所有点击（含继承来的入口、召唤、挑战、准备、结算点击）都会先随机等待
click_delay_min~click_delay_max 秒（默认 1.5~3 秒），通过重写 click /
appear_then_click 统一实现，父类里自带的等待会被自动识别，不会重复等。
"""
import random
from time import sleep, time

from module.base.timer import Timer
from module.exception import TaskEnd
from module.logger import logger

from tasks.ChaoGuiWang.script_task import ScriptTask as ChaoGuiWangTask, STAR_NAMES
from tasks.ChaoGuiWangAssist.assets import ChaoGuiWangAssistAssets
from tasks.ChaoGuiWangAssist.config import ChaoGuiWangAssistMode
from tasks.GameUi.page import page_main

TASK_NAME = 'ChaoGuiWangAssist'

# 「自己发现」等鬼面图标的最长时间与检查间隔（秒）
SELF_DEMON_TIMEOUT = 45
SELF_DEMON_INTERVAL = 3
# 分享弹窗打开 / 关闭的等待上限（秒）
SHARE_POPUP_TIMEOUT = 20
# 点卡片后等「挑战」按钮出现的时间（秒）
CHALLENGE_APPEAR_TIMEOUT = 20
# 等待超时后允许的连续无进展次数，超过后做一次界面诊断
WAIT_ROUND_BEFORE_DIAGNOSE = 3
# 分享弹窗没有关闭按钮，只能点弹窗外的空白处让它收起。
# 候选点都放在左侧「好友发现」的空白区域（鬼面空位、「暂无好友发现鬼王」文字），
# 刻意避开右下角「挑战」按钮 —— 那里点了会直接开打，属于邀请模式绝不允许的动作。
SHARE_BLANK_POINTS = [(170, 600), (170, 500)]


class ScriptTask(ChaoGuiWangTask, ChaoGuiWangAssistAssets):
    """超鬼王协战：邀请模式 / 协战模式。"""

    # 点击前随机等待的「已等待」豁免截止时间，避开父类自带的等待造成双重等待
    _skip_click_deadline = 0.0
    _delay_enabled = True

    # ------------------------------------------------------------------
    # 推送
    # ------------------------------------------------------------------
    def push(self, title: str, content: str, enabled: bool = True) -> bool:
        """统一走框架消息推送，受「总开关 + 该场景开关」双重控制。"""
        cfg = self.config.chao_gui_wang_assist.chao_gui_wang_assist_config
        if not cfg.notify_enable:
            logger.info(f'消息推送总开关已关闭，跳过推送：{title}')
            return False
        if not enabled:
            logger.info(f'该场景推送开关已关闭，跳过推送：{title}')
            return False
        try:
            self.config.notifier.push(title=title, content=content)
            return True
        except Exception as e:
            logger.exception(f'消息推送失败: {e}')
            return False

    # ------------------------------------------------------------------
    # 统一点击延迟：每次点击前随机等待 click_delay_min~click_delay_max 秒
    # ------------------------------------------------------------------
    def click_delay_range(self) -> tuple:
        """读取配置的随机等待区间，下限大于上限时自动对调，避免 random.uniform 报错。"""
        cfg = self.config.chao_gui_wang_assist.chao_gui_wang_assist_config
        low = float(cfg.click_delay_min)
        high = float(cfg.click_delay_max)
        if high < low:
            low, high = high, low
        return low, high

    def _sleep_before_click(self, label: str) -> float:
        """真正执行随机等待；不设置豁免标记，供每一次独立点击使用。"""
        if not self._delay_enabled:
            return 0.0
        low, high = self.click_delay_range()
        if high <= 0:
            return 0.0
        wait = random.uniform(low, high)
        logger.info(f'点击{label}前随机等待 {wait:.2f} 秒')
        sleep(wait)
        return wait

    def wait_before_click(self, label: str, delay: tuple = None) -> float:
        """父类同名方法：先等待，再登记 2 秒内的豁免，避免随后那次 click 再等一遍。

        父类的结算点击、界面恢复会在 click 之前自行调用本方法，重写后既能保留
        「等待 → 复核界面 → 再点」的语义，又不会因为 click 内部也等待而变成双倍。
        """
        if not self._delay_enabled:
            return 0.0
        low, high = delay if delay else self.click_delay_range()
        if high <= 0:
            return 0.0
        wait = random.uniform(low, high)
        logger.info(f'点击{label}前随机等待 {wait:.2f} 秒')
        sleep(wait)
        self._skip_click_deadline = time() + 2.0
        return wait

    def _consume_or_sleep(self, label: str) -> None:
        """若刚刚已经显式等待过就跳过，否则补一次随机等待。"""
        if self._skip_click_deadline and time() < self._skip_click_deadline:
            self._skip_click_deadline = 0.0
            logger.info(f'点击{label}前已完成随机等待，直接点击')
            return
        self._sleep_before_click(label)

    def click(self, click=None, interval: float = None) -> bool:
        """重写父类点击：保留 interval 限流语义，点击前插入随机等待。"""
        if not click:
            return False
        if interval:
            timer = self.interval_timer.get(click.name)
            if timer is None or timer.limit != interval:
                self.interval_timer[click.name] = Timer(interval)
            if not self.interval_timer[click.name].reached():
                return False
        self._consume_or_sleep(click.name)
        ok = super().click(click)
        if interval:
            self.interval_timer[click.name].reset()
            return True
        return ok

    def appear_then_click(self, target, action=None, interval: float = None,
                          threshold: float = None, duration: float = None) -> bool:
        """重写父类「出现即点击」：命中后先随机等待，等待结束再复核一次界面。

        等待期间界面可能自己切走（结算界面自动关闭、弹窗自行消失），
        因此点下去之前必须重新确认目标还在，否则会点到别的按钮上。
        """
        if not self.appear(target, interval=interval, threshold=threshold):
            return False
        self._consume_or_sleep(target.name)
        self.screenshot()
        if not self.appear(target, threshold=threshold):
            logger.info(f'随机等待期间 {target.name} 已消失，放弃本次点击')
            return False
        if action is None:
            x, y = target.coord()
        else:
            x, y = action.coord()
        self.device.click(x, y, control_name=target.name)
        return True

    def click_stage(self, image, action=None, timeout: int = 20, stable: int = 1,
                    delay: float = 0.6, pre_delay: tuple = None) -> bool:
        """父类的 click_stage 自带 pre_delay 等待，这里统一交给 appear_then_click。

        否则「click_stage 等一次 + appear_then_click 再等一次」会变成 3~6 秒，
        远超需求的 1.5~3 秒。
        """
        return super().click_stage(image, action=action, timeout=timeout,
                                   stable=stable, delay=delay, pre_delay=None)

    def click_cgw_entry(self) -> bool:
        """庭院活动列的入口点击：父类直接用 device.click，这里补上随机等待。"""
        self.screenshot()
        image = self.match_cgw_entry()
        if image is None:
            return False
        x, y = image.front_center()
        self._sleep_before_click('庭院超鬼王活动图标')
        self.device.click(x, y, control_name='CGW_ENTRY')
        logger.info(f'点击右侧活动列 ({x},{y}) 处的超鬼王图标')
        return True

    # ------------------------------------------------------------------
    # 通用等待
    # ------------------------------------------------------------------
    def wait_appear(self, image, timeout: int = 20, interval: float = 0.5) -> bool:
        """等待某个模板出现，期间顺手关掉奖励弹窗。"""
        timer = Timer(timeout).start()
        while 1:
            self.screenshot()
            if self.appear(image):
                return True
            if self.ui_reward_appear_click():
                continue
            if timer.reached():
                return False
            sleep(interval)

    def huozhu_ready(self) -> bool:
        """祸主降临主界面是否已就绪。

        与父类用单一鬼面图标判定不同，这里把「自己发现已有卡片」「召唤/挑战
        按钮可见」「好友发现空位」都算就绪，否则上一轮留下的鬼王卡片会让
        入口判断一直等到超时。
        """
        if self.appear(self.I_CGW_DEMON_ICON):
            return True
        if self.appear(self.I_CGWA_GATHER_SELF):
            return True
        if self.appear(self.I_CGW_SUMMON) or self.appear(self.I_CGW_CHALLENGE):
            return True
        if self.appear(self.I_CGWA_FRIEND_MASK) or self.appear(self.I_CGWA_FRIEND_CARD):
            return True
        return False

    def open_huozhu(self) -> bool:
        """点击「祸主降临」，直到主界面就绪。"""
        timer = Timer(30).start()
        while 1:
            self.screenshot()
            if self.huozhu_ready():
                logger.info('已进入祸主降临界面')
                return True
            if self.appear_then_click(self.I_CGW_HUOZHU,
                                      action=self.C_CGW_HUOZHU_CLICK, interval=2):
                continue
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1.5):
                continue
            if timer.reached():
                logger.warning('进入祸主降临界面超时')
                return False
            sleep(0.5)

    def enter_cgw(self) -> bool:
        """步骤 1、2：庭院 → 活动图标 → 祸主降临，失败时诊断界面并按流程恢复。"""
        if not self.enter_activity():
            logger.warning('未能进入活动，先做界面诊断恢复')
            if not self.diagnose_and_recover('进入活动入口'):
                return False
        if not self.open_huozhu():
            logger.warning('未能进入祸主降临，先做界面诊断恢复')
            if not self.diagnose_and_recover('进入祸主降临界面'):
                return False
        return True

    # ------------------------------------------------------------------
    # 邀请模式
    # ------------------------------------------------------------------
    def ticket_empty_confirmed(self, times: int = 2, interval: float = 1.0) -> bool:
        """连续多次确认「门票已打完」，避免单帧误判。

        可召唤界面上圆形图标下方有一排橙色勾玉，门票打完时勾玉消失、图标变金色。
        但界面切换动画期间勾玉可能整帧被遮住，只判一帧会把「正要召唤」误判成
        「门票已打完」并直接把任务结束掉，所以必须连续两帧都成立才采信
        （实机日志显示真的有鬼王可召唤时一定是能读到星级的）。
        """
        for i in range(times):
            self.screenshot()
            if not self.is_ticket_empty():
                return False
            if i + 1 < times:
                sleep(interval)
        return True

    def wait_self_demon(self) -> str:
        """等「自己发现」区域的鬼面图标，返回四种状态。

        'READY'    有鬼王可召唤（鬼面图标或「召唤鬼王」按钮可见，且门票没打完）；
        'EXISTING' 已有自己的鬼王卡片（集结印章可见），跳过召唤直接分享；
        'EMPTY'    连续确认门票已打完（图标变金色鬼面、下方没有勾玉）；
        'NONE'     超时且以上都不是。

        判定顺序是「已有卡片 → 门票空 → 可召唤」：已有卡片时圆形图标区域
        可能是空的，先判卡片才能识别出「上一只还没分享完」；门票判定放在
        可召唤之前，是因为门票打完后鬼面图标依旧存在（实测 0.998 命中），
        若先判可召唤就会一直停在 READY 上反复点召唤。
        """
        timer = Timer(SELF_DEMON_TIMEOUT).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_CGWA_GATHER_SELF):
                logger.info('「自己发现」已有鬼王卡片，本轮跳过召唤，直接分享')
                return 'EXISTING'
            if self.ticket_empty_confirmed():
                return 'EMPTY'
            if self.appear(self.I_CGW_DEMON_ICON) or self.appear(self.I_CGW_SUMMON):
                return 'READY'
            if self.ui_reward_appear_click():
                continue
            if timer.reached():
                logger.warning(f'等待「自己发现」鬼面图标超时（{SELF_DEMON_TIMEOUT} 秒）')
                return 'NONE'
            sleep(SELF_DEMON_INTERVAL)

    def wait_demon_return(self, cfg) -> str:
        """分享后等好友把鬼王打掉、鬼面重新出现。

        每 wait_interval 秒检查一次，最多等 wait_timeout 秒：
        'READY'   鬼面重新出现 → 好友击杀成功；
        'EMPTY'   门票已打完（金色鬼面），本任务随之结束；
        'TIMEOUT' 超时（好友始终没打完），由调用方推送提醒后继续等。
        """
        timer = Timer(cfg.wait_timeout).start()
        logger.info(f'等待好友击杀，每 {cfg.wait_interval} 秒检查一次，'
                    f'最长等待 {cfg.wait_timeout} 秒')
        while 1:
            self.screenshot()
            if self.appear(self.I_CGW_DEMON_ICON):
                logger.info('「自己发现」鬼面重新出现，判定好友已击杀鬼王')
                return 'READY'
            # 卡片还在说明好友还没打完；此时圆形图标区域的金色像素不代表门票耗尽，
            # 所以只在没有卡片时才做门票判定，避免误判成任务完成
            if not self.appear(self.I_CGWA_GATHER_SELF) and self.ticket_empty_confirmed():
                return 'EMPTY'
            if self.ui_reward_appear_click():
                continue
            if timer.reached():
                logger.warning(f'等待好友击杀超时（{cfg.wait_timeout} 秒）')
                return 'TIMEOUT'
            sleep(cfg.wait_interval)

    def wait_share_popup(self, timeout: int = SHARE_POPUP_TIMEOUT) -> bool:
        """等待分享弹窗打开（以弹窗独有的「公开」按钮为准）。"""
        if self.wait_appear(self.I_CGWA_SHARE, timeout=timeout):
            logger.info('鬼王分享弹窗已打开')
            return True
        logger.warning('点击「集结」后未等到分享弹窗')
        return False

    def wait_share_closed(self, timeout: int = SHARE_POPUP_TIMEOUT) -> bool:
        """等待分享弹窗关闭（「公开」按钮连续两帧消失）。"""
        timer = Timer(timeout).start()
        gone = 0
        while 1:
            self.screenshot()
            if self.appear(self.I_CGWA_SHARE):
                gone = 0
            else:
                gone += 1
                if gone >= 2:
                    logger.info('分享弹窗已关闭')
                    return True
            if timer.reached():
                logger.warning('分享弹窗未关闭')
                return False
            sleep(0.5)

    def close_share_popup(self, timeout: int = 10) -> bool:
        """不邀请任何人时把分享弹窗关掉。

        弹窗上没有关闭按钮（右上角只有说明文字），只能点弹窗外的空白处
        让它自行收起；挨个候选点各试一次，一旦收起立刻返回，避免多点。
        """
        self.screenshot()
        if not self.appear(self.I_CGWA_SHARE):
            return True
        for x, y in SHARE_BLANK_POINTS:
            self._sleep_before_click('分享弹窗外的空白处')
            self.device.click(x, y, control_name='CGWA_SHARE_BLANK')
            if self.wait_share_closed(timeout=timeout):
                logger.info(f'点击空白处 ({x},{y}) 后分享弹窗已关闭')
                return True
            logger.warning(f'点击空白处 ({x},{y}) 后分享弹窗仍在，换一个位置再试')
        return False

    def select_friend(self, name: str) -> str:
        """在当前页签里按名字找好友卡片，找到就点一下选中，返回命中的名字。

        两列各做一次整列 OCR，把识别到的文字与配置的好友名比对；
        完全相等优先，其次做互相包含的宽松匹配，容忍个别错字。
        """
        self.screenshot()
        for rule in (self.O_CGWA_NAME_LEFT, self.O_CGWA_NAME_RIGHT):
            try:
                results = rule.detect_and_ocr(self.device.image)
            except Exception as e:
                logger.warning(f'好友名识别异常：{e}')
                continue
            for item in results:
                text = item.ocr_text.replace(' ', '').strip()
                if not text:
                    continue
                if not (text == name or name in text or text in name):
                    continue
                box = item.box
                cx = int(rule.roi[0] + (box[0][0] + box[2][0]) / 2)
                cy = int(rule.roi[1] + (box[0][1] + box[2][1]) / 2)
                self._sleep_before_click(f'好友「{text}」')
                self.device.click(cx, cy, control_name='CGWA_FRIEND')
                logger.info(f'已选中好友「{text}」，点击坐标 ({cx},{cy})')
                sleep(1.0)
                return text
        return ''

    def share_to_friend(self, cfg) -> str:
        """在分享弹窗里只找配置指定的那位好友，找到就点一下选中。

        先在「好友」页签找，找不到再切「寮友」页签找（指定好友也可能在寮里）。
        两个页签都没有就返回空串——绝不改点列表里的其他人，也不点「公开」。
        """
        name = (cfg.share_friend_name or '').strip()
        if not name:
            logger.error('未填写【分享好友名】：邀请模式只邀请指定好友，不做兜底选择')
            return ''
        picked = self.select_friend(name)
        if picked:
            return picked
        logger.info(f'「好友」页签未找到 {name}，切到「寮友」页签再找')
        self.click(self.C_CGWA_SHARE_TAB_GUILD)
        sleep(1.5)
        picked = self.select_friend(name)
        if picked:
            return picked
        logger.warning(f'「好友」与「寮友」两个页签都没有找到 {name}')
        return ''

    def share_demon(self, cfg) -> str:
        """点「集结」→ 选中指定好友 → 点「邀请」→ 等弹窗关闭。

        返回 'OK'             已成功邀请指定好友；
            'FRIEND_MISSING' 弹窗里没有指定好友，不邀请任何人并关掉弹窗；
            'FAIL'           集结印章没出现 / 弹窗没打开 / 点了邀请但没关掉。
        """
        if not self.wait_appear(self.I_CGWA_GATHER_SELF, timeout=CHALLENGE_APPEAR_TIMEOUT):
            logger.warning('未等到「集结」印章，无法分享鬼王')
            return 'FAIL'
        self.click(self.I_CGWA_GATHER_SELF)
        if not self.wait_share_popup():
            return 'FAIL'
        picked = self.share_to_friend(cfg)
        if not picked:
            logger.error(f'分享弹窗里没有指定好友「{cfg.share_friend_name}」，'
                         f'为避免误邀请其他人，本次不做邀请，直接关闭弹窗')
            self.close_share_popup()
            return 'FRIEND_MISSING'
        logger.info(f'已选中指定好友「{picked}」，点击「邀请」')
        self.click(self.C_CGWA_SHARE_INVITE)
        return 'OK' if self.wait_share_closed() else 'FAIL'

    def run_invite(self, cfg) -> tuple:
        """邀请模式主循环，返回 (邀请成功次数, 结束原因, 结束类型)。

        结束类型用于决定结束推送的标题与开关：
        COMPLETE / STAR_CLEARED / FAILED。
        """
        invited = 0
        timeouts = 0
        while 1:
            logger.hr(f'超鬼王协战·邀请模式，已成功邀请 {invited} 只', 2)
            state = self.wait_self_demon()

            if state == 'EMPTY':
                return invited, (f'所有鬼王门票已打完（图标显示金色鬼面），'
                                 f'共成功邀请 {invited} 只'), 'COMPLETE'

            if state == 'NONE':
                timeouts += 1
                logger.warning(f'未等到鬼面图标（连续第 {timeouts} 次），做一次界面诊断')
                if not self.diagnose_and_recover('等待自己发现的鬼面图标') \
                        or timeouts >= cfg.max_consecutive_timeout:
                    return invited, (f'等待鬼面图标超时且界面恢复失败，任务提前结束，'
                                     f'共成功邀请 {invited} 只'), 'FAILED'
                continue

            if state == 'READY':
                star = self.read_star_with_retry()
                if star:
                    logger.info(f'识别到鬼王星级：{STAR_NAMES.get(star, star)}')
                else:
                    logger.warning('未能识别鬼王星级，按当前勾选状态继续')
                if star and not self.star_allowed(star, cfg):
                    return invited, (f'识别到 {STAR_NAMES.get(star, star)} 鬼王，'
                                     f'不在勾选的星级范围内，说明所选星级已打完，'
                                     f'共成功邀请 {invited} 只'), 'STAR_CLEARED'
                # 不点挑战，只召唤；召唤后卡片出现即可点集结
                if not self.click_stage(self.I_CGW_SUMMON, self.C_CGW_BOTTOM_BUTTON,
                                        timeout=20, delay=1.0):
                    timeouts += 1
                    logger.warning(f'点击「召唤鬼王」超时（连续第 {timeouts} 次）')
                    if not self.diagnose_and_recover('点击召唤鬼王') \
                            or timeouts >= cfg.max_consecutive_timeout:
                        return invited, (f'召唤鬼王失败且界面恢复失败，任务提前结束，'
                                         f'共成功邀请 {invited} 只'), 'FAILED'
                    continue

            # READY 已召唤 / EXISTING 已有卡片：只分享给指定好友
            status = self.share_demon(cfg)
            if status == 'FRIEND_MISSING':
                # 只邀请特定好友，找不到就不邀请任何人，也不改分享给别人，直接结束
                return invited, (f'分享弹窗里没有指定好友「{cfg.share_friend_name}」，'
                                 f'按设置不邀请其他人，任务结束，共成功邀请 {invited} 只'), \
                       'FRIEND_MISSING'
            if status != 'OK':
                timeouts += 1
                logger.warning(f'分享鬼王失败（连续第 {timeouts} 次），做一次界面诊断')
                if not self.diagnose_and_recover('分享鬼王') \
                        or timeouts >= cfg.max_consecutive_timeout:
                    return invited, (f'连续 {timeouts} 次分享失败且界面恢复失败，任务提前结束，'
                                     f'共成功邀请 {invited} 只'), 'FAILED'
                continue

            timeouts = 0
            # 分享已发出，等好友把这只鬼王打掉（鬼面重新出现）
            wait_rounds = 0
            while 1:
                result = self.wait_demon_return(cfg)
                if result == 'READY':
                    invited += 1
                    logger.info(f'好友已击杀鬼王，邀请成功，累计 {invited} 只')
                    break
                if result == 'EMPTY':
                    return invited, (f'所有鬼王门票已打完（图标显示金色鬼面），'
                                     f'共成功邀请 {invited} 只'), 'COMPLETE'
                # TIMEOUT：按配置推送提醒后继续等，不结束任务
                wait_rounds += 1
                self.push('超鬼王协战·邀请超时',
                          f'分享后等待 {cfg.wait_timeout} 秒仍未等到好友击杀，'
                          f'任务继续等待下一只。当前已成功邀请 {invited} 只。',
                          enabled=cfg.notify_invite_timeout)
                if wait_rounds % WAIT_ROUND_BEFORE_DIAGNOSE == 0:
                    logger.warning(f'已连续 {wait_rounds} 轮等待超时，做一次界面诊断确认仍在活动界面')
                    if not self.diagnose_and_recover('等待好友击杀'):
                        return invited, (f'等待好友击杀期间界面恢复失败，任务提前结束，'
                                         f'共成功邀请 {invited} 只'), 'FAILED'

    # ------------------------------------------------------------------
    # 协战模式
    # ------------------------------------------------------------------
    def friend_demon_state(self) -> str:
        """「好友发现」区域的状态。

        'MASK'    鬼面空位图标 → 当前没有可协战的好友鬼王；
        'PUBLIC'  鬼王卡片带「已公开」印章 → 按需求忽略；
        'PRIVATE' 存在鬼王卡片且没有「已公开」印章 → 非公开鬼王，可以协战；
        'UNKNOWN' 三种特征都没有 → 界面异常，交给诊断恢复。

        注意「好友发现」的卡片上没有「集结」印章（那是「自己发现」卡片才有的），
        所以卡片存在与否用卡片固定的「剩余时间」标签判定。
        """
        if self.appear(self.I_CGWA_FRIEND_MASK):
            return 'MASK'
        if self.appear(self.I_CGWA_PUBLIC):
            return 'PUBLIC'
        if self.appear(self.I_CGWA_FRIEND_CARD):
            return 'PRIVATE'
        return 'UNKNOWN'

    def assist_once(self, cfg) -> str:
        """点好友鬼王卡片 → 挑战 → 准备 → 结算，返回 'WIN' / 'LOSE' / 'GONE' / 'TIMEOUT'。

        协战不召唤、也不打自己发现的鬼王：因此每一轮都必须先点「好友发现」的
        卡片把右侧面板切到好友的鬼王，再点「挑战」。不能像以前那样「挑战按钮
        已经可见就直接打」——自己发现的鬼王同样会显示挑战按钮，那样会把
        自己的鬼王打掉。
        """
        self.last_timeout_stage = ''
        self.screenshot()
        if not self.appear(self.I_CGWA_FRIEND_CARD):
            logger.info('点卡片前「好友发现」的鬼王卡片已消失（可能被别人打掉了），本轮放弃')
            return 'GONE'
        self.click(self.C_CGWA_CARD_FRIEND)
        sleep(1.0)
        if not self.wait_appear(self.I_CGW_CHALLENGE, timeout=CHALLENGE_APPEAR_TIMEOUT):
            # 少数情况下第一次点击只是取消了之前的选中，补点一次再等
            logger.warning('点击好友鬼王卡片后未出现「挑战」按钮，补点一次卡片')
            self.click(self.C_CGWA_CARD_FRIEND)
            if not self.wait_appear(self.I_CGW_CHALLENGE, timeout=CHALLENGE_APPEAR_TIMEOUT):
                self.last_timeout_stage = '打开好友鬼王卡片'
                return 'TIMEOUT'
        logger.info('已选中「好友发现」的鬼王，开始协战（不召唤、不打自己的鬼王）')
        return self.fight_once(cfg, need_summon=False)

    def run_assist(self, cfg) -> tuple:
        """协战模式主循环，返回 (协战成功次数, 结束原因, 结束类型)。

        结束类型用于决定结束推送的标题与开关：
        NO_ASSIST / DEFEAT_LIMIT / FAILED。
        """
        assists = 0
        defeats = 0
        timeouts = 0
        wait_timer = None
        while 1:
            logger.hr(f'超鬼王协战·协战模式，已成功协战 {assists} 只', 2)
            state = self.friend_demon_state()

            if state in ('MASK', 'PUBLIC'):
                if state == 'MASK':
                    logger.info('「好友发现」为空（鬼面可见），等待好友发现鬼王')
                else:
                    logger.info('好友发现的鬼王已公开，按需求忽略，继续等待')
                if wait_timer is None:
                    wait_timer = Timer(cfg.wait_timeout).start()
                    logger.info(f'开始等待，每 {cfg.wait_interval} 秒检查一次，'
                                f'最长 {cfg.wait_timeout} 秒')
                if wait_timer.reached():
                    return assists, (f'等待 {cfg.wait_timeout} 秒仍无可用协战鬼王，'
                                     f'共成功协战 {assists} 只'), 'NO_ASSIST'
                if self.ui_reward_appear_click():
                    continue
                sleep(cfg.wait_interval)
                continue

            if state == 'PRIVATE':
                wait_timer = None
                result = self.assist_once(cfg)
                if result == 'WIN':
                    assists += 1
                    defeats = 0
                    timeouts = 0
                    logger.info(f'协战成功，累计 {assists} 只')
                    continue
                if result == 'GONE':
                    # 卡片在点击前消失：不打任何东西，回到循环重新看「好友发现」
                    logger.info('好友鬼王已消失，重新检查「好友发现」区域')
                    continue
                if result == 'LOSE':
                    defeats += 1
                    logger.warning(f'协战失败，连续失败 {defeats}/{cfg.max_consecutive_defeat} 次')
                    if defeats >= cfg.max_consecutive_defeat:
                        return assists, (f'连续 {defeats} 次协战失败，可能是御魂或阵容配置有问题，'
                                         f'任务已停止，共成功协战 {assists} 只'), 'DEFEAT_LIMIT'
                    # 失败后重新找卡片再打一次
                    if not self.diagnose_and_recover('协战失败后返回'):
                        return assists, (f'协战失败后界面恢复失败，任务提前结束，'
                                         f'共成功协战 {assists} 只'), 'FAILED'
                    continue
                # TIMEOUT
                timeouts += 1
                stage = self.last_timeout_stage or '协战流程'
                logger.warning(f'{stage} 环节超时（连续第 {timeouts} 次），做一次界面诊断')
                if not self.diagnose_and_recover(stage) \
                        or timeouts >= cfg.max_consecutive_timeout:
                    return assists, (f'{stage} 环节连续超时且界面恢复失败，任务提前结束，'
                                     f'共成功协战 {assists} 只'), 'FAILED'
                continue

            # UNKNOWN：界面异常，交给诊断恢复
            logger.warning('「好友发现」区域三种特征都未命中，先做界面诊断恢复')
            if not self.diagnose_and_recover('协战主界面'):
                return assists, (f'界面无法识别且恢复失败，任务提前结束，'
                                 f'共成功协战 {assists} 只'), 'FAILED'
            wait_timer = None

    # ------------------------------------------------------------------
    # 界面诊断：补充本任务独有的分享弹窗
    # ------------------------------------------------------------------
    def diagnose_and_recover(self, stage: str) -> bool:
        """父类的诊断表里没有分享弹窗，先把它处理掉再交给父类流程恢复。

        关弹窗只能点空白处：弹窗里的「邀请」按钮会邀请当前选中的那位好友，
        在恢复流程里点它等于随便抓一个人赠送鬼王，违反「只邀请指定好友」。
        """
        self.screenshot()
        if self.appear(self.I_CGWA_SHARE):
            logger.warning('界面诊断：当前停留在鬼王分享弹窗，点空白处关掉它')
            if not self.close_share_popup(timeout=10):
                logger.warning('分享弹窗仍未关闭，继续交给父类的通用返回流程')
        return super().diagnose_and_recover(stage)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(self):
        conf = self.config.chao_gui_wang_assist
        cfg = conf.chao_gui_wang_assist_config
        mode = cfg.mode
        logger.info(f'超鬼王协战启动，模式：{mode.value}')

        # 邀请模式下三个星级开关都没勾时没有任何可分享的目标，不进游戏，直接退出
        if mode == ChaoGuiWangAssistMode.INVITE and not (
                cfg.star_rank_1_4 or cfg.star_rank_5 or cfg.star_rank_6):
            logger.error('模式为「邀请模式」但【打1至4星鬼王】【打5星鬼王】【打6星鬼王】'
                         '一个都没勾选，没有可召唤分享的目标，任务直接结束。'
                         '请至少勾选一个星级，或把模式改为「协战模式」。')
            self.set_next_run(task=TASK_NAME, success=False, finish=True)
            raise TaskEnd(TASK_NAME)

        # 邀请模式：只邀请指定好友，没填好友名就没有目标，不做兜底选择
        if mode == ChaoGuiWangAssistMode.INVITE and not (cfg.share_friend_name or '').strip():
            logger.error('模式为「邀请模式」但【分享好友名】为空。邀请模式只把鬼王邀请给'
                         '指定的那一位好友，不会随便选列表里的其他人，任务直接结束。'
                         '请填写好友名，或把模式改为「协战模式」。')
            self.push('超鬼王协战·任务完全失败',
                      '未填写【分享好友名】，邀请模式只邀请指定好友、不做兜底选择，任务未执行。',
                      enabled=cfg.notify_task_failed)
            self.set_next_run(task=TASK_NAME, success=False, finish=True)
            raise TaskEnd(TASK_NAME)

        if cfg.click_delay_max < cfg.click_delay_min:
            logger.warning(f'点击随机等待下限 {cfg.click_delay_min} 秒大于上限 '
                           f'{cfg.click_delay_max} 秒，运行时按对调后的区间处理')

        # 只有协战模式需要打斗，才切换御魂；邀请模式全程不战斗，不做多余动作，
        # 只确保停在庭院（活动入口从这里找）
        if mode == ChaoGuiWangAssistMode.ASSIST:
            self.switch_soul_before_run(conf.switch_soul_config)
        else:
            self.goto_page(page_main)

        if not self.enter_cgw():
            self.push('超鬼王协战·任务完全失败',
                      '未能进入祸主降临界面，界面诊断恢复也未成功，任务结束，'
                      '请检查活动是否已开放。',
                      enabled=cfg.notify_task_failed)
            self.set_next_run(task=TASK_NAME, success=False, finish=True)
            raise TaskEnd(TASK_NAME)

        if mode == ChaoGuiWangAssistMode.INVITE:
            count, reason, kind = self.run_invite(cfg)
            unit = '只鬼王邀请成功'
        else:
            count, reason, kind = self.run_assist(cfg)
            unit = '只鬼王协战成功'

        logger.info(reason)
        # 按需求：成功次数不在过程中逐条推送，只在任务结束时汇总一次，
        # 标题按结束类型区分，各场景开关独立控制是否发送
        titles = {
            'COMPLETE': ('超鬼王协战·任务完成', cfg.notify_task_complete),
            'STAR_CLEARED': ('超鬼王协战·打完所选星级鬼王', cfg.notify_star_cleared),
            'NO_ASSIST': ('超鬼王协战·无协战鬼王', cfg.notify_no_assist),
            'FRIEND_MISSING': ('超鬼王协战·未找到指定好友', cfg.notify_friend_missing),
            'DEFEAT_LIMIT': ('超鬼王协战·连续协战失败', cfg.notify_defeat_limit),
            'FAILED': ('超鬼王协战·任务完全失败', cfg.notify_task_failed),
            'END': ('超鬼王协战·任务汇总', cfg.notify_summary),
        }
        title, enabled = titles.get(kind, titles['END'])
        self.push(title, f'{reason}。本次共 {count} {unit}。', enabled=enabled)

        success = count > 0 or kind == 'COMPLETE'
        self.set_next_run(task=TASK_NAME, success=success, finish=True)
        raise TaskEnd(TASK_NAME)
