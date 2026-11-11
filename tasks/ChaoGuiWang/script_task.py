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
每一处推送都可以在任务配置里单独关闭，另有 notify_enable 总开关。

任务卡住/超时时会把所有界面模板逐个匹配一遍并打印分数，判定当前停在哪个界面，
再按「结算 → 战斗 → 准备 → 挑战 → 主界面 → 活动 → 庭院」的流程把游戏推回第 3 步。
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

# 准备阶段的节奏参数（单位：秒）。
# 游戏在界面切换的头几帧会吞掉点击，模拟器越慢越明显，可按需调大这些值。
PREPARE_STABLE_FRAMES = 2       # 准备按钮连续出现多少帧才认为准备界面已加载完毕
PREPARE_FRAME_INTERVAL = 0.6    # 稳定检查的截图间隔
PREPARE_AFTER_CHALLENGE = 2.0   # 点完「挑战」后留给游戏加载准备界面的时间
PREPARE_AFTER_CLICK = 1.5       # 每次点完「准备」后等待游戏响应的时间
PREPARE_CONFIRM_TIMEOUT = 45    # 确认「已进入战斗」的总超时
PREPARE_RETRY_INTERVAL = 2.5    # 两次补点之间的最小间隔，也作为首次点击的宽限期
PREPARE_RETRY_LIMIT = 6         # 最多补点次数

# 点击「召唤鬼王」「挑战」按钮之前的随机等待区间（秒）。
# 只在这两个按钮已经稳定出现之后等待，既模拟人工反应，也错开界面切换动画。
BEFORE_CLICK_DELAY = (1.5, 3.0)

# 超时后界面诊断与流程恢复的边界，避免恢复逻辑本身变成新的死循环
RECOVER_TIMEOUT = 120           # 单次恢复最长秒数
RECOVER_MAX_ACTIONS = 15        # 单次恢复最多执行多少个「推进动作」
RECOVER_UNKNOWN_LIMIT = 6       # 连续多少轮识别不出界面就放弃恢复
RECOVER_REWARD_LIMIT = 5        # 最多连续关多少次奖励弹窗，避免弹窗判定不停空转


class ScriptTask(RightActivity, SwitchSoul, GeneralBattleAssets, ChaoGuiWangAssets):
    """超鬼王（祸主降临）任务。"""

    # ------------------------------------------------------------------
    # 推送
    # ------------------------------------------------------------------
    def push(self, title: str, content: str, enabled: bool = True) -> bool:
        """统一走框架消息推送，受「总开关 + 该场景开关」双重控制。

        enabled 由调用方传入对应场景的配置项，例如 cfg.notify_round_timeout；
        总开关 notify_enable 在这里统一读取，关掉后所有推送都不再发送。
        返回是否真的调用了推送，便于调用方记录；不影响任务流程。

        notify_config 形如：
            provider: pushplus
            token: 你的pushplus_token
        未开启框架推送时其内部会安全地直接返回，不影响任务流程。
        """
        cfg = self.config.chao_gui_wang.chao_gui_wang_config
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
    # 步骤 1：从庭院进入活动
    # ------------------------------------------------------------------
    def cgw_entry_visible(self) -> bool:
        """庭院右侧是否存在超鬼王图标。

        模板只取圆形图标内部的蓝色法阵核心（38x38），不含会随动画变化的
        金色外圈、飘带、竖排文字和光晕。五张不同帧的实机截图上分数稳定在
        0.95 以上，其它界面最高仅 0.39，因此阈值可以提到 0.7。
        仍保留两个不同帧的核心模板作为兜底，任一命中即认为图标存在。
        """
        return self.appear(self.I_CGW_ENTRY) or self.appear(self.I_CGW_ENTRY_ALT)

    def wait_huozhu_page(self, timeout: int = 15) -> bool:
        """点击入口后等待活动界面出现；入口图标连续消失 3 帧也视为已离开庭院。"""
        timer = Timer(timeout).start()
        gone_count = 0
        while 1:
            self.screenshot()
            if self.appear(self.I_CGW_HUOZHU) or self.appear(self.I_CGW_DEMON_ICON):
                return True
            # 不依赖活动主界面模板；连续 3 帧不见入口图标才判定已离开庭院，
            # 避免图标光效动画的某一帧同时躲过两个模板而造成误判
            if self.cgw_entry_visible():
                gone_count = 0
            else:
                gone_count += 1
                if gone_count >= 3:
                    return True
            if self.ui_reward_appear_click():
                continue
            if timer.reached():
                return False
            sleep(0.5)

    def enter_activity(self) -> bool:
        """步骤 1：点击庭院右侧的超鬼王活动图标。

        不再复用 RightActivity.enter()：该组件在目标识别失败时会无限点击切换按钮，
        既会把活动栏切到没有超鬼王的一页，又会触发 GameTooManyClickError。
        这里改为「确认图标 → 固定区域点击 → 校验已离开庭院」，最多尝试 4 次。
        """
        for attempt in range(1, 5):
            self.screenshot()
            if not self.cgw_entry_visible():
                logger.info(f'第 {attempt}/4 次：右侧活动栏未见超鬼王图标，切换活动栏')
                self.appear_then_click(self.I_TOGGLE_BUTTON, interval=2)
                sleep(1.5)
                continue
            logger.info(f'第 {attempt}/4 次：点击庭院右侧超鬼王活动图标')
            self.click(self.C_CGW_ENTRY_CLICK)
            if self.wait_huozhu_page(timeout=15):
                logger.info('已进入超鬼王活动界面')
                return True
            logger.warning('点击入口后仍停在庭院，返回庭院重试')
            self.goto_page(page_main)
            sleep(1)
        return False

    # ------------------------------------------------------------------
    # 步骤 2：点击祸主降临
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

    def click_stage(self, image, action=None, timeout: int = 20,
                    stable: int = 1, delay: float = 0.6,
                    pre_delay: tuple = None) -> bool:
        """等待按钮稳定出现后点击一次，超时返回 False。

        stable > 1 时要求连续多帧都识别到按钮，避免界面切换动画期间
        「按钮刚画出来、还不可点」就把点击打出去；delay 是点击后留给
        游戏加载下一界面的缓冲时间。
        pre_delay 形如 (1.5, 3.0)，表示「按钮已经出现、但还没点下去」之前
        插入一段随机等待；等待结束后会重新截图复核按钮是否还在，避免
        等待期间界面自己切走之后还对着旧界面点一下。
        """
        timer = Timer(timeout).start()
        seen = 0
        while 1:
            self.screenshot()
            if self.appear(image):
                seen += 1
            else:
                seen = 0
            ready = seen >= stable
            if ready and pre_delay:
                wait = random.uniform(pre_delay[0], pre_delay[1])
                logger.info(f'点击 {image.name} 前随机等待 {wait:.2f} 秒')
                sleep(wait)
                # 等待期间界面可能已经切换或弹出奖励，复核一次再决定要不要点
                self.screenshot()
                if not self.appear(image):
                    logger.warning(f'随机等待期间 {image.name} 已消失，重新等待按钮出现')
                    ready = False
                    seen = 0
            if ready:
                if action is None:
                    clicked = self.appear_then_click(image, interval=1.2)
                else:
                    clicked = self.appear_then_click(image, action=action, interval=1.2)
                if clicked:
                    logger.info(f'点击 {image.name}')
                    sleep(delay)
                    return True
            if self.ui_reward_appear_click():
                continue
            if timer.reached():
                logger.warning(f'等待 {image.name} 超时（{timeout} 秒）')
                return False
            sleep(PREPARE_FRAME_INTERVAL if stable > 1 else 0.3)

    def confirm_battle_started(self) -> bool:
        """步骤 7 的闭环：确认「准备」点击真的生效，没生效就间隔补点。

        旧逻辑点一次「准备」就直接进入等结算循环，若点击被界面切换动画吞掉，
        就会一直停在准备界面，直到 200 秒战斗超时。这里用三种证据确认开战：
          1. 出现战斗信息图标 I_BATTLE_INFO（准备界面实测不匹配）；
          2. 出现胜利/结算画面（战斗可能很快结束）；
          3. 准备按钮连续 3 帧不再出现。
        仍在准备界面时按固定间隔补点，最多 PREPARE_RETRY_LIMIT 次。
        """
        timer = Timer(PREPARE_CONFIRM_TIMEOUT).start()
        grace = Timer(PREPARE_RETRY_INTERVAL).start()
        retries = 0
        gone = 0
        while 1:
            self.screenshot()
            if (self.appear(self.I_BATTLE_INFO)
                    or self.appear(self.I_CGW_SETTLE)
                    or self.appear(self.I_WIN)):
                logger.info('已确认进入战斗')
                return True
            if self.appear(self.I_PREPARE_HIGHLIGHT):
                # 高亮准备按钮还在，说明上一次点击没生效
                gone = 0
                if grace.reached() and retries < PREPARE_RETRY_LIMIT:
                    if self.click(self.C_CGW_PREPARE_CLICK, interval=PREPARE_RETRY_INTERVAL):
                        retries += 1
                        # 连续补点同一按钮会触发框架的防卡死保护，清掉点击记录
                        self.device.click_record_clear()
                        logger.warning(f'准备界面未响应，第 {retries}/{PREPARE_RETRY_LIMIT} 次补点「准备」')
                        grace.reset()
                        sleep(PREPARE_AFTER_CLICK)
                        continue
            elif self.appear(self.I_PREPARE_DARK):
                # 按钮变暗通常表示已准备、正在等待开战，只等不点
                gone = 0
            else:
                gone += 1
                if gone >= 3:
                    logger.info('准备按钮已消失，战斗开始')
                    return True
            if self.ui_reward_appear_click():
                continue
            if timer.reached():
                logger.warning(f'确认进入战斗超时（{PREPARE_CONFIRM_TIMEOUT} 秒，补点 {retries} 次）')
                return False
            sleep(0.5)

    def battle_once(self, cfg) -> bool:
        """完成一次击杀：召唤 → 挑战 → 准备 → 等待结算 → 点击绿框返回主界面。

        任一环节超时都会把具体阶段记到 self.last_timeout_stage，
        交给外层做「逐个模板诊断界面 + 按流程恢复」。
        """
        self.last_timeout_stage = ''
        # 步骤 5：召唤鬼王。按钮出现后先随机等 1.5~3 秒再点
        if not self.click_stage(self.I_CGW_SUMMON, self.C_CGW_BOTTOM_BUTTON,
                                timeout=20, delay=1.0, pre_delay=BEFORE_CLICK_DELAY):
            self.last_timeout_stage = '召唤鬼王'
            return False
        # 步骤 6：挑战。同样先随机等待，点完再多等一会儿，
        # 别在准备界面还没加载出来时就把点击打出去
        if not self.click_stage(self.I_CGW_CHALLENGE, self.C_CGW_BOTTOM_BUTTON,
                                timeout=20, delay=PREPARE_AFTER_CHALLENGE,
                                pre_delay=BEFORE_CLICK_DELAY):
            self.last_timeout_stage = '挑战鬼王'
            return False
        # 步骤 7：准备按钮连续两帧稳定后再点，并改用按钮中心的固定点击区域
        if not self.click_stage(self.I_PREPARE_HIGHLIGHT, self.C_CGW_PREPARE_CLICK,
                                timeout=30, stable=PREPARE_STABLE_FRAMES,
                                delay=PREPARE_AFTER_CLICK):
            self.last_timeout_stage = '点击准备'
            return False
        # 确认游戏真的开战了；没响应就间隔补点，避免卡在准备界面直到战斗超时
        if not self.confirm_battle_started():
            self.last_timeout_stage = '确认进入战斗（准备界面）'
            return False

        # 确认开战之后才开始计战斗超时，避免把准备阶段算进去
        # 长战斗需登记白名单，避免被设备的卡死检测误判
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        logger.info('已确认开战，等待鬼王战斗结束')
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
                self.last_timeout_stage = '等待战斗结算'
                return False
            sleep(0.3)

    # ------------------------------------------------------------------
    # 卡住超时：逐个模板诊断界面 + 按游戏流程恢复
    # ------------------------------------------------------------------
    def page_templates(self) -> list:
        """界面判定表，顺序即流程回溯的优先级。

        每项为 (界面标识, 中文说明, 该界面的候选模板)。同一界面多个模板任一
        命中即可；越靠前的界面越接近一轮战斗的末尾，因为超时时游戏多半停在
        结算/准备/战斗，而不是已经回到主界面。
        注意 I_CGW_DEMON_ICON 与 I_CGW_SUMMON 会同时出现在第 3 步界面上，
        所以「第 3 步已就绪」要排在「召唤按钮可见」前面。
        """
        return [
            ('SETTLE', '战斗结算界面', [self.I_CGW_SETTLE, self.I_WIN]),
            ('PREPARE', '战斗准备界面', [self.I_PREPARE_HIGHLIGHT, self.I_PREPARE_DARK]),
            ('BATTLE', '鬼王战斗中', [self.I_BATTLE_INFO]),
            ('CHALLENGE', '鬼王卡片 / 挑战界面', [self.I_CGW_CHALLENGE]),
            ('MAIN_READY', '祸主降临主界面（第 3 步已就绪）', [self.I_CGW_DEMON_ICON]),
            ('MAIN_SUMMON', '祸主降临主界面（召唤按钮可见）', [self.I_CGW_SUMMON]),
            ('ACTIVITY', '超鬼王活动主界面', [self.I_CGW_HUOZHU]),
            ('COURTYARD', '庭院（右侧超鬼王入口）', [self.I_CGW_ENTRY, self.I_CGW_ENTRY_ALT]),
        ]

    def match_templates(self) -> dict:
        """把所有模板逐个在当前截图上匹配一遍，返回 模板名 -> (是否命中, 分数)。

        这里刻意不在第一个命中处短路：超时往往是「某个模板分数卡在阈值附近」
        造成的，把全部分数打进日志才能一眼看出游戏究竟停在哪一帧。
        """
        result = {}
        for _, _, images in self.page_templates():
            for image in images:
                try:
                    hit = image.match(self.device.image)
                    result[image.name] = (bool(hit), float(image.last_score))
                except Exception as e:
                    logger.warning(f'模板 {image.name} 匹配异常：{e}')
                    result[image.name] = (False, -1.0)
        return result

    def detect_current_page(self, scores: dict) -> str:
        """依据逐模板匹配结果，按流程优先级判定当前界面标识。"""
        for page, label, images in self.page_templates():
            if any(scores.get(image.name, (False, 0))[0] for image in images):
                logger.info(f'界面判定：{label} ({page})')
                return page
        logger.warning('界面判定：所有模板均未命中，当前界面未知')
        return 'UNKNOWN'

    def log_page_diagnosis(self, stage: str) -> str:
        """超时时重新截图，逐个匹配模板并输出分数，返回界面标识。"""
        self.screenshot()
        scores = self.match_templates()
        logger.hr(f'超时界面诊断：{stage}', 2)
        for name, (hit, score) in scores.items():
            logger.info(f'  模板 {name}: 分数 {score:.4f} → {"命中" if hit else "未命中"}')
        return self.detect_current_page(scores)

    def diagnose_and_recover(self, stage: str) -> bool:
        """卡住/超时的统一入口：逐个匹配模板判断界面，再按游戏流程推回第 3 步。

        返回 True 表示已经回到祸主降临主界面，主流程可以继续下一轮；
        返回 False 表示多次诊断仍无法识别界面或推进次数/时间用尽，应当结束任务。
        """
        logger.warning(f'{stage} 卡住超时，开始逐个匹配模板诊断界面')
        timer = Timer(RECOVER_TIMEOUT).start()
        actions = 0
        unknown = 0
        reward_clicks = 0
        while 1:
            page = self.log_page_diagnosis(f'{stage}（第 {actions + 1} 次推进）')
            if page in ('MAIN_READY', 'MAIN_SUMMON'):
                logger.info('已回到祸主降临主界面，界面恢复完成')
                return True
            if timer.reached() or actions >= RECOVER_MAX_ACTIONS:
                logger.error(f'界面恢复结束：超时或推进次数用尽（{actions} 次）')
                return False
            if page != 'UNKNOWN':
                unknown = 0
            # 奖励/公告弹窗会盖在任意界面上，先关掉再按界面推进；
            # 但限制连续关闭次数，避免弹窗判定一直返回 True 造成空转
            if reward_clicks < RECOVER_REWARD_LIMIT and self.ui_reward_appear_click():
                reward_clicks += 1
                logger.info(f'检测到奖励/弹窗，先关闭再重新诊断（第 {reward_clicks} 次）')
                sleep(0.8)
                continue

            if page == 'SETTLE':
                # 第 8 步：结算界面，点击绿框任意一处回到第 3 步
                self.click(random.choice(self.settle_click_areas()), interval=1.2)
                self.device.click_record_clear()
                logger.info('结算界面 → 点击绿框区域返回主界面')
                sleep(1.2)
            elif page == 'PREPARE':
                # 第 7 步：仍在准备界面，说明「准备」被吞了，补一点让战斗继续
                if self.appear(self.I_PREPARE_HIGHLIGHT):
                    self.click(self.C_CGW_PREPARE_CLICK, interval=PREPARE_RETRY_INTERVAL)
                    self.device.click_record_clear()
                    logger.info('准备界面 → 补点「准备」推进战斗')
                    sleep(PREPARE_AFTER_CLICK)
                else:
                    logger.info('准备按钮已变暗（正在等待开战），只等不点')
                    sleep(2)
            elif page == 'BATTLE':
                # 战斗中不做任何点击，等结算画面出现；纯等待不计入推进动作
                logger.info('战斗中 → 等待结算界面出现')
                sleep(3)
                continue
            elif page == 'CHALLENGE':
                # 第 6 步：挑战按钮还在，随机等待后补点挑战
                wait = random.uniform(*BEFORE_CLICK_DELAY)
                logger.info(f'挑战界面 → 随机等待 {wait:.2f} 秒后点击「挑战」')
                sleep(wait)
                self.screenshot()
                if self.appear(self.I_CGW_CHALLENGE):
                    self.click(self.C_CGW_BOTTOM_BUTTON, interval=1.2)
                    self.device.click_record_clear()
                    sleep(PREPARE_AFTER_CHALLENGE)
            elif page == 'ACTIVITY':
                # 第 2 步：停在活动主界面，点「祸主降临」回到第 3 步
                self.click(self.C_CGW_HUOZHU_CLICK, interval=2)
                self.device.click_record_clear()
                logger.info('活动主界面 → 点击「祸主降临」')
                sleep(1.5)
            elif page == 'COURTYARD':
                # 第 1 步：还在庭院，重新点右侧超鬼王入口
                self.click(self.C_CGW_ENTRY_CLICK, interval=2)
                self.device.click_record_clear()
                logger.info('庭院 → 点击右侧超鬼王活动图标')
                sleep(2)
            else:
                # 界面未知：通常是子页面或弹窗，按通用返回/确认逐个尝试
                unknown += 1
                if unknown >= RECOVER_UNKNOWN_LIMIT:
                    logger.error(f'连续 {unknown} 次无法识别界面，放弃恢复')
                    return False
                handled = False
                for image in (self.I_UI_CONFIRM_SAMLL, self.I_UI_CONFIRM,
                              self.I_UI_BACK_BLUE, self.I_UI_BACK_YELLOW, self.I_UI_BACK_RED):
                    if self.appear_then_click(image, interval=1.5):
                        self.device.click_record_clear()
                        logger.info(f'界面未知 → 点击通用按钮 {image.name} 尝试返回')
                        handled = True
                        break
                if not handled:
                    logger.warning('界面未知且无通用返回按钮，等待界面自行刷新')
                sleep(2)
            actions += 1

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
        # 失败时先逐个模板诊断界面并按流程恢复，恢复不回来才推送并结束
        if not self.enter_activity():
            logger.warning('步骤 1 未能进入活动，先做界面诊断恢复')
            if not self.diagnose_and_recover('进入活动入口'):
                self.push('超鬼王·未找到活动入口',
                          '庭院右侧未能进入超鬼王活动，界面诊断恢复也未成功，'
                          '任务结束，请检查活动是否已开放。',
                          enabled=cfg.notify_entry_failed)
                self.set_next_run(task='ChaoGuiWang', success=False, finish=True)
                raise TaskEnd('ChaoGuiWang')
        if not self.open_huozhu():
            logger.warning('步骤 2 未能进入祸主降临，先做界面诊断恢复')
            if not self.diagnose_and_recover('进入祸主降临界面'):
                self.push('超鬼王·进入活动失败',
                          '未能进入「祸主降临」界面，界面诊断恢复也未成功，'
                          '任务结束，请检查活动是否已开放。',
                          enabled=cfg.notify_open_failed)
                self.set_next_run(task='ChaoGuiWang', success=False, finish=True)
                raise TaskEnd('ChaoGuiWang')

        # 全局要求 b：按设置的击杀数量循环
        while killed < cfg.kill_count:
            logger.hr(f'超鬼王 第 {killed + 1}/{cfg.kill_count} 只', 2)

            # 步骤 3：等待鬼面图标
            if not self.wait_demon_icon(cfg):
                self.push('超鬼王·等待图标超时',
                          f'等待「自己发现」鬼面图标超过 {cfg.wait_icon_timeout} 秒，'
                          f'已击杀 {killed}/{cfg.kill_count} 只，正在诊断界面并尝试恢复。',
                          enabled=cfg.notify_wait_icon_timeout)
                consecutive_timeout += 1
                recovered = self.diagnose_and_recover('等待鬼面图标')
                if not recovered or consecutive_timeout >= cfg.max_consecutive_timeout:
                    stop_reason = '界面恢复失败或连续超时，任务终止'
                    break
                continue

            # 步骤 4：疲劳度判定（严格「超过」阈值才停止）
            fatigue = self.get_fatigue()
            if fatigue is None:
                self.push('超鬼王·疲劳度识别失败',
                          '右上角疲劳度连续识别失败，为避免超疲劳，'
                          f'任务停止。已击杀 {killed} 只。',
                          enabled=cfg.notify_fatigue_ocr_failed)
                # 该场景已有专属通知，无论开关是否开启都不再补发通用的结束通知
                final_pushed = True
                stop_reason = '疲劳度识别失败'
                break
            logger.info(f'当前疲劳度 {fatigue}/100，阈值 {cfg.fatigue_limit}')
            if fatigue > cfg.fatigue_limit:
                self.push('超鬼王·疲劳度已达上限',
                          f'当前疲劳度 {fatigue}/100，超过设定阈值 {cfg.fatigue_limit}，'
                          f'任务停止。已击杀 {killed}/{cfg.kill_count} 只。',
                          enabled=cfg.notify_fatigue_limit)
                final_pushed = True
                stop_reason = '疲劳度超过阈值'
                break

            # 步骤 5~8：完成一轮计一只
            if not self.battle_once(cfg):
                stage = self.last_timeout_stage or '本轮战斗'
                self.push('超鬼王·本轮超时',
                          f'{stage} 环节超时（含准备确认 {PREPARE_CONFIRM_TIMEOUT} 秒、'
                          f'战斗上限 {cfg.battle_timeout} 秒），已击杀 {killed}/{cfg.kill_count} 只，'
                          f'正在诊断界面并尝试恢复。',
                          enabled=cfg.notify_round_timeout)
                consecutive_timeout += 1
                recovered = self.diagnose_and_recover(stage)
                if not recovered or consecutive_timeout >= cfg.max_consecutive_timeout:
                    stop_reason = '界面恢复失败或连续超时，任务终止'
                    break
                continue

            killed += 1
            consecutive_timeout = 0
            logger.info(f'已击杀超鬼王 {killed}/{cfg.kill_count} 只')

        # 结束推送与调度
        if killed >= cfg.kill_count:
            self.push('超鬼王·任务完成',
                      f'已完成设定的击杀数量 {killed} 只超鬼王，任务结束。',
                      enabled=cfg.notify_task_complete)
        elif not final_pushed and stop_reason:
            self.push('超鬼王·任务提前结束',
                      f'结束原因：{stop_reason}；已击杀 {killed}/{cfg.kill_count} 只。',
                      enabled=cfg.notify_task_aborted)

        logger.info(f'超鬼王任务结束，共击杀 {killed} 只')
        self.set_next_run(task='ChaoGuiWang', success=True, finish=True)
        raise TaskEnd('ChaoGuiWang')
