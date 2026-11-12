# This Python file uses the following encoding: utf-8
"""超鬼王 · 祸主降临。

流程：
    1. 庭院点击最右侧的超鬼王活动图标
    2. 点击「祸主降临」文字
    3. 等待「自己发现」区域的鬼面图标出现（每 5 秒检查一次，最多 40 秒）
    4. 读取右上角疲劳度，超过阈值则推送并停止
    5. 召唤鬼王前先识别鬼王星级（圆形图标下方的橙色勾玉数量 = 星级）
    6. 点击右下角「召唤鬼王」
    7. 点击右下角「挑战」
    8. 点击右下角「准备」，等待鬼王打完（最多 200 秒）
    9. 结算界面点击用户标注的三个安全区域之一
   10. 胜利计入一只并回到第 3 步；失败不计入，直接继续点「挑战」重打同一只鬼王

结束条件：
    - 按数量模式：击杀数达到设定值 → 推送「打完所设置数量的鬼王」
    - 按星级模式：识别到未勾选的星级 → 推送「打完所选星级鬼王」
    - 任意模式：圆形图标变成金色鬼面且没有橙色勾玉 → 门票耗尽，推送「任务完成」
    - 疲劳度超过阈值 → 推送「超过疲劳阈值」
    - 连续挑战失败达到上限 → 推送「连续挑战失败」
    - 找不到活动入口或界面恢复失败 → 推送「任务完全失败」

消息推送统一走框架通知 self.config.notifier.push()，
在「脚本设置 → 错误」里开启推送并配置 provider: pushplus 即为 PushPlus 推送。
以上六类推送各有独立开关，另有 notify_enable 总开关，每条推送都带已击杀数量。

任务卡住/超时时会把所有界面模板逐个匹配一遍并打印分数，判定当前停在哪个界面，
再按「结算 → 战斗 → 准备 → 挑战 → 主界面 → 活动 → 庭院」的流程把游戏推回第 3 步。
"""
import random
from time import sleep

import cv2

from module.base.timer import Timer
from module.exception import TaskEnd
from module.logger import logger

from tasks.ChaoGuiWang.assets import ChaoGuiWangAssets
from tasks.ChaoGuiWang.config import ChaoGuiWangTarget
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
PREPARE_CONFIRM_TIMEOUT = 45    # 确认「准备」点击生效的总超时
PREPARE_RETRY_INTERVAL = 2.5    # 两次补点之间的最小间隔
# 点完「准备」后不立刻下结论，先观察按钮是否消失；窗口内消失即成功。
# 单纯一帧截图容易被界面切换动画骗到，所以再要求连续几帧都看不到高亮按钮
PREPARE_OBSERVE_WINDOW = 3.0    # 每次点击后的观察窗口（秒）
PREPARE_GONE_FRAMES = 2         # 连续多少帧判定「准备按钮已消失」
PREPARE_RETRY_LIMIT = 6         # 最多补点次数

# 点击「召唤鬼王」「挑战」按钮之前的随机等待区间（秒）。
# 只在这两个按钮已经稳定出现之后等待，既模拟人工反应，也错开界面切换动画。
BEFORE_CLICK_DELAY = (1.5, 3.0)

# 庭院活动入口的尝试次数。图标在右侧活动列五个槽位里的位置不固定，
# 每次尝试都重新全列搜索并点命中位置，搜索不到才切换活动栏分页。
ENTRY_RETRY_LIMIT = 6

# 超时后界面诊断与流程恢复的边界，避免恢复逻辑本身变成新的死循环
RECOVER_TIMEOUT = 120           # 单次恢复最长秒数
RECOVER_MAX_ACTIONS = 15        # 单次恢复最多执行多少个「推进动作」
RECOVER_UNKNOWN_LIMIT = 6       # 连续多少轮识别不出界面就放弃恢复
RECOVER_REWARD_LIMIT = 5        # 最多连续关多少次奖励弹窗，避免弹窗判定不停空转

# ---------------- 鬼王星级识别（坐标基于 1280x720）----------------
# 召唤鬼王界面「全部队列」左侧的圆形图标，下方是一排橙色勾玉，
# 亮起来的勾玉个数就是鬼王星级；勾玉间距固定 8px，起点也固定。
STAR_ROW_ROI = (1058, 526, 58, 17)
# 主判据是「橙色像素的横向跨度」：第 n 颗勾玉的右边缘固定在 8n-2 像素处，
# 因此 span = 8n-2，与亮度无关。实测 1~6 星的跨度是 6/14/22/28/36/44，
# 对应 rank = round((span+2)/8)，对 ±20 的亮度扰动仍然全部判对。
STAR_PILL_PITCH = 8.0
STAR_PILL_SPAN_OFFSET = 2.0
STAR_COLUMN_MIN = 3            # 一列至少有这么多橙色像素才算勾玉本体，过滤光晕
# 橙色像素下限只用来判断「有没有勾玉」（门票打空 / 界面未加载），不参与星级判定。
# 原先用面积除以每星像素数做交叉校验，但光晕脉冲会整体抬高面积（实机恒把
# 3 星读成 4 星），且结果从不参与任何分支，已移除。
STAR_ORANGE_MIN = 12
# 圆形图标本体，门票打空时这里会变成金色鬼面具，且下方没有橙色勾玉
TICKET_ICON_ROI = (1050, 485, 65, 67)
TICKET_GOLD_MIN = 100

STAR_NAMES = {1: '1星', 2: '2星', 3: '3星', 4: '4星', 5: '5星', 6: '6星'}


class ScriptTask(RightActivity, SwitchSoul, GeneralBattleAssets, ChaoGuiWangAssets):
    """超鬼王（祸主降临）任务。"""

    # ------------------------------------------------------------------
    # 推送
    # ------------------------------------------------------------------
    def push(self, title: str, content: str, enabled: bool = True) -> bool:
        """统一走框架消息推送，受「总开关 + 该场景开关」双重控制。

        enabled 由调用方传入对应场景的配置项，例如 cfg.notify_fatigue_limit；
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
    def match_cgw_entry(self):
        """在右侧活动列里找出当前帧命中入口模板的规则对象，未命中返回 None。

        超鬼王图标的槽位不固定（右侧活动列五个槽位都可能出现），所以两个
        模板的搜索区都放大到了整列（`1140,110,145,450`）。RuleImage.match()
        命中后会把 roi_front 改写成实际命中的绝对坐标，因此点击必须取
        `front_center()`，不能再点写死的固定点。
        """
        for image in (self.I_CGW_ENTRY, self.I_CGW_ENTRY_ALT):
            if self.appear(image):
                return image
        return None

    def cgw_entry_visible(self) -> bool:
        """庭院右侧活动列是否存在超鬼王图标。

        模板只取圆形图标内部的蓝色法阵核心（38x38），不含会随动画变化的
        金色外圈、飘带、竖排文字和光晕。五张不同帧的实机截图上分数稳定在
        0.95 以上，其它界面最高仅 0.43，因此阈值可以提到 0.7。
        仍保留两个不同帧的核心模板作为兜底，任一命中即认为图标存在。
        """
        return self.match_cgw_entry() is not None

    def click_cgw_entry(self) -> bool:
        """点击当前帧命中的入口图标；没命中就不点，避免误点其它活动图标。"""
        self.screenshot()
        image = self.match_cgw_entry()
        if image is None:
            return False
        x, y = image.front_center()
        self.device.click(x, y, control_name='CGW_ENTRY')
        logger.info(f'点击右侧活动列 ({x},{y}) 处的超鬼王图标')
        return True

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
        """步骤 1：点击庭院右侧活动列里的超鬼王活动图标。

        图标在右侧活动列的五个槽位里位置不固定，所以先全列搜索模板，
        命中后点命中的那个圆心；搜索不到才去切换活动栏分页。
        不复用 RightActivity.enter()：该组件在目标识别失败时会无限点击切换按钮，
        既会把活动栏切到没有超鬼王的一页，又会触发 GameTooManyClickError。
        """
        entry_misses = 0
        for attempt in range(1, ENTRY_RETRY_LIMIT + 1):
            if not self.click_cgw_entry():
                entry_misses += 1
                logger.info(f'第 {attempt}/{ENTRY_RETRY_LIMIT} 次：活动列未见超鬼王图标'
                            f'（连续 {entry_misses} 次）')
                # 只是加载慢的话再截一帧就够；连续两次都没有才切活动栏分页
                if entry_misses >= 2 and entry_misses % 2 == 0:
                    logger.info('连续未见图标，切换活动栏分页再找')
                    self.appear_then_click(self.I_TOGGLE_BUTTON, interval=2)
                sleep(1.5)
                continue
            entry_misses = 0
            logger.info(f'第 {attempt}/{ENTRY_RETRY_LIMIT} 次：已点击入口，等待活动界面')
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
    def wait_demon_icon(self, cfg) -> str:
        """等待「自己发现」区域红框内的鬼面图标，返回三种状态。

        'READY'    鬼面图标已出现，可以召唤鬼王；
        'EXISTING' 没等到鬼面图标，但界面上已经有可挑战的鬼王（挑战按钮可见、
                   召唤按钮不可见），此时不必召唤，直接开打，打完同样算击杀一只；
        'NONE'     超时，且没有可打的鬼王。

        之所以要区分 'EXISTING'：上一只鬼王挑战失败后重进界面、或活动期间
        已有他人/已发现但未召唤的鬼王时，界面直接停在鬼王卡片 + 挑战按钮，
        红框里根本不会出现鬼面图标，只等图标就会一直超时空转（实机
        23:45 因此连续超时并触发 GameStuckError）。
        """
        timer = Timer(cfg.wait_icon_timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_CGW_DEMON_ICON):
                return 'READY'
            if (self.appear(self.I_CGW_CHALLENGE)
                    and not self.appear(self.I_CGW_SUMMON)):
                logger.info('界面上已有可挑战的鬼王（挑战按钮可见、无召唤按钮），跳过召唤直接开打')
                return 'EXISTING'
            if timer.reached():
                logger.warning(f'等待鬼面图标超时（{cfg.wait_icon_timeout} 秒），'
                               f'且未发现可挑战的鬼王')
                return 'NONE'
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
    # 步骤 5：鬼王星级与门票判定
    # ------------------------------------------------------------------
    def _hsv(self, area) -> 'cv2.Mat':
        """截取指定区域并转成 HSV，坐标按 1280x720 绝对像素。"""
        x, y, w, h = area
        return cv2.cvtColor(self.device.image[y:y + h, x:x + w], cv2.COLOR_RGB2HSV)

    def read_demon_star(self) -> int:
        """数圆形图标下方亮起的橙色勾玉，几个勾玉就是几星。

        返回 1~6；返回 0 表示没有检测到勾玉（门票打空，或是界面还没加载好）。

        判据用「橙色像素的横向跨度」而不是像素面积：勾玉按 8px 固定间距排布，
        跨度只取决于点亮了几颗，和光晕强弱无关；面积会被图标弹出时的辉光
        整体抬高（实机曾把 4 星读成 5 星），因此完全不参与判定。
        本帧的跨度与像素量记入 _star_diag，只在识别异常时用于诊断日志。
        """
        hsv = self._hsv(STAR_ROW_ROI)
        orange = ((hsv[..., 0] >= 5) & (hsv[..., 0] <= 28) &
                  (hsv[..., 1] >= 120) & (hsv[..., 2] >= 150))
        pixels = int(orange.sum())
        if pixels < STAR_ORANGE_MIN:
            self._star_diag = f'橙色 {pixels}px（低于下限 {STAR_ORANGE_MIN}）'
            return 0
        columns = [i for i, count in enumerate(orange.sum(axis=0))
                   if count >= STAR_COLUMN_MIN]
        if not columns:
            self._star_diag = f'橙色 {pixels}px，但无有效列'
            return 0
        span = columns[-1] - columns[0] + 1
        rank = int(round((span + STAR_PILL_SPAN_OFFSET) / STAR_PILL_PITCH))
        self._star_diag = f'跨度 {span}px，橙色 {pixels}px'
        return max(1, min(6, rank))

    def is_ticket_empty(self) -> bool:
        """所有鬼王门票都打完了：圆形图标变成金色鬼面，且下方没有橙色勾玉。"""
        if self.read_demon_star():
            return False
        hsv = self._hsv(TICKET_ICON_ROI)
        gold = ((hsv[..., 0] >= 15) & (hsv[..., 0] <= 42) &
                (hsv[..., 1] >= 80) & (hsv[..., 2] >= 95))
        pixels = int(gold.sum())
        if pixels >= TICKET_GOLD_MIN:
            logger.info(f'识别到金色鬼面图标（金色像素 {pixels}），门票已打完')
            return True
        return False

    def read_star_with_retry(self, times: int = 3) -> int:
        """星级图标偶尔会被界面动画遮住，连续识别到同一个结果才采信。

        正常情况不输出诊断信息；只有三次读数不一致、或最终仍识别不到星级时，
        才打一行包含每帧跨度 / 橙色像素量的记录，便于事后排障。
        """
        reads = []
        for _ in range(times):
            self.screenshot()
            star = self.read_demon_star()
            reads.append((star, getattr(self, '_star_diag', '')))
            if len(reads) >= 2 and star and star == reads[-2][0]:
                return star
            sleep(1)
        result = reads[-1][0] or 0
        if not result or len({star for star, _ in reads}) > 1:
            detail = ' | '.join(f'{star}星（{diag}）' for star, diag in reads)
            logger.warning(f'星级识别异常：{detail}，本轮采用 {result} 星')
        return result

    # 星级 → 配置字段名。协战任务继承本类，但它的星级开关叫 invite_star_N，会覆盖这张表。
    STAR_FLAG_FIELDS = {
        1: 'star_rank_1',
        2: 'star_rank_2',
        3: 'star_rank_3',
        4: 'star_rank_4',
        5: 'star_rank_5',
        6: 'star_rank_6',
    }

    def star_flags(self, cfg) -> dict:
        """返回 {星级: 是否勾选}，字段名取自 STAR_FLAG_FIELDS。"""
        return {rank: bool(getattr(cfg, field, False))
                for rank, field in self.STAR_FLAG_FIELDS.items()}

    def selected_stars_text(self, cfg) -> str:
        """把已勾选的星级拼成「1星、3星」这样的短文本，用于日志。"""
        flags = self.star_flags(cfg)
        picked = [f'{rank}星' for rank in sorted(flags) if flags[rank]]
        return '、'.join(picked) if picked else '无'

    def star_allowed(self, star: int, cfg) -> bool:
        """当前星级是否在勾选范围内。

        六个开关都没勾时不做兜底：任务在主流程开头就直接结束，
        这里一律返回 False，避免配置缺失时误打未勾选的星级。
        """
        return self.star_flags(cfg).get(star, False)

    # ------------------------------------------------------------------
    # 步骤 6~9：召唤、挑战、准备、战斗、结算
    # ------------------------------------------------------------------
    def settle_click_areas(self) -> list:
        """结算界面的可点击区域：用户白色框选的四大块，胜利与失败界面通用。

        四块分别是上方长条、左中、右中、底部条状区域，每次随机挑一块、
        块内再随机取点，避免总点同一处。上方区域的右边界刻意止于 x=1137，
        避开右侧的礼包/合成/积分按钮列。
        """
        return [self.C_CGW_SETTLE_TOP, self.C_CGW_SETTLE_LEFT,
                self.C_CGW_SETTLE_RIGHT, self.C_CGW_SETTLE_BOTTOM]

    def wait_before_click(self, label: str, delay: tuple = BEFORE_CLICK_DELAY) -> float:
        """点击前插入一段随机等待，默认 1.5~3 秒。

        结算界面的「胜利 / 失败」文字与淡入动画还没定格时点击会被游戏吞掉，
        点得太快就会一直停在结算界面，因此统一在点击前随机等一会儿，
        并把实际等待时长写进日志，方便事后复盘节奏是否合适。
        """
        wait = random.uniform(delay[0], delay[1])
        logger.info(f'点击{label}前随机等待 {wait:.2f} 秒')
        sleep(wait)
        return wait

    def settle_finished(self) -> bool:
        """结算界面是否已经关掉、回到可以继续操作的界面。

        以「结算模板都不在 + 挑战/召唤/鬼面图标任一出现」为准，
        不引用 I_WIN，避免残留的胜利图标把已关闭的界面判成还在结算。
        """
        if self.appear(self.I_CGW_SETTLE) or self.appear(self.I_CGW_SETTLE_FAIL):
            return False
        return (self.appear(self.I_CGW_CHALLENGE)
                or self.appear(self.I_CGW_SUMMON)
                or self.appear(self.I_CGW_DEMON_ICON))

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
        """步骤 8 的闭环：点完「准备」看按钮是否消失，没消失就 WARNING 重新点。

        实机日志（2026-11-13 03:49）显示：点「准备」到真正开战约 18.7 秒，
        这段时间高亮准备按钮一直可见。旧逻辑等的是 I_BATTLE_INFO（真正开战）
        才返回，结果固定会在 15 秒宽限结束时补点一次、每只鬼王都打 WARNING。
        现在只判断「这一下点进去没有」，判据换成准备按钮本身：
          1. 出现战斗信息 / 结算画面：已经开战，直接成功；
          2. 高亮准备按钮连续 PREPARE_GONE_FRAMES 帧不再出现，或按钮变成
             暗态 I_PREPARE_DARK（已准备）：点击已生效，成功；
          3. 高亮按钮仍在且观察窗口已过：点击被吞，打 WARNING 并重新点一次，
             最多 PREPARE_RETRY_LIMIT 次。
        补点日志级别保持 WARNING 不变。
        """
        timer = Timer(PREPARE_CONFIRM_TIMEOUT).start()
        retries = 0
        gone = 0
        # 首次点击已在 click_stage 里完成，先留一小段窗口让游戏响应，
        # 避免刚点完按钮还没来得及消失就误报
        observe = Timer(PREPARE_OBSERVE_WINDOW).start()
        while 1:
            self.screenshot()
            if (self.appear(self.I_BATTLE_INFO)
                    or self.appear(self.I_CGW_SETTLE)
                    or self.appear(self.I_CGW_SETTLE_FAIL)
                    or self.appear(self.I_WIN)):
                logger.info('已确认进入战斗')
                return True
            # 超时判定放在所有 continue 之前，避免奖励弹窗一直弹出时跳过超时
            if timer.reached():
                logger.warning(f'确认「准备」生效超时（{PREPARE_CONFIRM_TIMEOUT} 秒，'
                               f'重新点击 {retries} 次）')
                return False
            if self.ui_reward_appear_click():
                continue
            if (self.appear(self.I_PREPARE_DARK)
                    or not self.appear(self.I_PREPARE_HIGHLIGHT)):
                # 按钮变暗（已准备）或已消失，都说明这一下点进去了
                gone += 1
                if gone >= PREPARE_GONE_FRAMES:
                    logger.info('准备按钮已消失，「准备」点击成功')
                    return True
            else:
                gone = 0
                if observe.reached() and retries < PREPARE_RETRY_LIMIT:
                    if self.click(self.C_CGW_PREPARE_CLICK, interval=PREPARE_RETRY_INTERVAL):
                        retries += 1
                        # 连续补点同一按钮会触发框架的防卡死保护，清掉点击记录
                        self.device.click_record_clear()
                        logger.warning(f'准备按钮未消失，第 {retries}/{PREPARE_RETRY_LIMIT} 次重新点击「准备」')
                        observe = Timer(PREPARE_OBSERVE_WINDOW).start()
                        sleep(PREPARE_AFTER_CLICK)
                        continue
            sleep(0.5)

    def wait_settle(self, cfg) -> str:
        """战斗中等待结算界面，返回 'WIN' / 'LOSE' / 'TIMEOUT'。

        胜利与失败共用同一套布局，但标题文字不同：胜利是金色「胜利」，
        失败是灰白「失败」，因此用两个模板分别判定。实测失败界面在胜利模板上
        只有 0.27 分，胜利界面在失败模板上只有 0.31 分，互不误判。
        """
        # 长战斗需登记白名单，避免被设备的卡死检测误判
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        logger.info('已确认开战，等待鬼王战斗结束')
        timer = Timer(cfg.battle_timeout).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_CGW_SETTLE) or self.appear(self.I_WIN):
                self.device.stuck_record_clear()
                logger.info('结算界面：胜利')
                return 'WIN'
            if self.appear(self.I_CGW_SETTLE_FAIL):
                self.device.stuck_record_clear()
                logger.warning('结算界面：挑战失败（未在限定时间内击杀）')
                return 'LOSE'
            if self.ui_reward_appear_click():
                continue
            if timer.reached():
                logger.warning(f'等待鬼王战斗结束超时（{cfg.battle_timeout} 秒）')
                self.device.stuck_record_clear()
                return 'TIMEOUT'
            sleep(0.3)

    def leave_settle(self, result: str) -> bool:
        """在结算界面点击绿框标注的区域，直到回到可继续操作的界面。

        胜利后回到祸主降临主界面（第 3 步界面），失败后回到同一只鬼王的挑战界面。
        每次点击前都随机等待 1.5~3 秒（wait_before_click）：结算文字与淡入动画
        没定格时点击会被吞掉；等待结束后先复核界面，已经自己关掉就不再补点。
        """
        label = '胜利结算' if result == 'WIN' else '失败结算'
        for i in range(1, 9):
            self.wait_before_click(f'{label}区域（第 {i} 次）')
            self.screenshot()
            if self.settle_finished():
                logger.info(f'{label}在随机等待期间已自行关闭')
                return True
            self.click(random.choice(self.settle_click_areas()), interval=1.2)
            self.device.click_record_clear()
            sleep(1.0)
            self.screenshot()
            if self.settle_finished():
                logger.info(f'已离开结算界面（第 {i} 次点击）')
                return True
            if self.ui_reward_appear_click():
                continue
        logger.warning('点击结算区域后仍停留在结算界面')
        return False

    def fight_once(self, cfg, need_summon: bool) -> str:
        """打完一次：召唤（可选）→ 挑战 → 准备 → 战斗 → 结算点击。

        need_summon 为 False 表示挑战失败后重打同一只鬼王，此时不会再出现
        「召唤鬼王」按钮，直接点「挑战」即可。
        返回 'WIN' / 'LOSE' / 'TIMEOUT'；超时时把阶段写进 self.last_timeout_stage，
        交给外层做「逐个模板诊断界面 + 按流程恢复」。
        """
        self.last_timeout_stage = ''
        if need_summon:
            # 步骤 6：召唤鬼王。按钮出现后先随机等 1.5~3 秒再点
            if not self.click_stage(self.I_CGW_SUMMON, self.C_CGW_BOTTOM_BUTTON,
                                    timeout=20, delay=1.0, pre_delay=BEFORE_CLICK_DELAY):
                self.last_timeout_stage = '召唤鬼王'
                return 'TIMEOUT'
        # 步骤 7：挑战。同样先随机等待，点完再多等一会儿，
        # 别在准备界面还没加载出来时就把点击打出去
        if not self.click_stage(self.I_CGW_CHALLENGE, self.C_CGW_BOTTOM_BUTTON,
                                timeout=20, delay=PREPARE_AFTER_CHALLENGE,
                                pre_delay=BEFORE_CLICK_DELAY):
            self.last_timeout_stage = '挑战鬼王'
            return 'TIMEOUT'
        # 步骤 8：准备按钮连续两帧稳定后，先随机等 1.5~3 秒再点。
        # 准备界面刚从「挑战」切过来时按钮虽已画出但还不可点，点太早会被吞；
        # 召唤/挑战早就有这段等待，准备按钮是唯一漏掉的。
        # 点完由 confirm_battle_started 检查按钮是否消失来确认点击生效
        if not self.click_stage(self.I_PREPARE_HIGHLIGHT, self.C_CGW_PREPARE_CLICK,
                                timeout=30, stable=PREPARE_STABLE_FRAMES,
                                delay=PREPARE_AFTER_CLICK,
                                pre_delay=BEFORE_CLICK_DELAY):
            self.last_timeout_stage = '点击准备'
            return 'TIMEOUT'
        if not self.confirm_battle_started():
            self.last_timeout_stage = '确认进入战斗（准备界面）'
            return 'TIMEOUT'

        result = self.wait_settle(cfg)
        if result == 'TIMEOUT':
            self.last_timeout_stage = '等待战斗结算'
            return 'TIMEOUT'
        # 步骤 9：结算界面点击绿框区域返回
        self.leave_settle(result)
        return result

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
            ('SETTLE', '战斗结算界面（胜利或失败）', [self.I_CGW_SETTLE,
                                                  self.I_CGW_SETTLE_FAIL, self.I_WIN]),
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
            if page in ('MAIN_READY', 'MAIN_SUMMON', 'CHALLENGE'):
                logger.info('已回到可继续操作的界面，界面恢复完成')
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
                # 第 9 步：结算界面，点击绿框任意一处返回。
                # 与正常流程一致，点击前先随机等 1.5~3 秒，等结算文字/动画定格
                self.wait_before_click('结算界面（恢复流程）')
                self.screenshot()
                if self.settle_finished():
                    logger.info('结算界面已自行关闭，无需补点')
                    continue
                self.click(random.choice(self.settle_click_areas()), interval=1.2)
                self.device.click_record_clear()
                logger.info('结算界面 → 点击绿框区域返回')
                sleep(1.2)
            elif page == 'PREPARE':
                # 第 8 步：仍在准备界面，说明「准备」被吞了，补一点让战斗继续
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
                # 第 7 步：挑战按钮还在，随机等待后补点挑战
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
                # 第 1 步：还在庭院，重新找活动列里的超鬼王图标再点（位置不固定）
                if self.click_cgw_entry():
                    self.device.click_record_clear()
                    logger.info('庭院 → 点击右侧活动列里的超鬼王活动图标')
                    sleep(2)
                else:
                    logger.warning('庭院里没找到超鬼王图标，等待活动列刷新')
                    sleep(1.5)
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
        by_star = cfg.target_mode == ChaoGuiWangTarget.STAR
        killed = 0
        defeats = 0
        timeouts = 0
        need_summon = True

        logger.info(f'结束条件：{cfg.target_mode.value}'
                    + ('' if by_star else f'，目标 {cfg.kill_count} 只')
                    + (f'，星级开关：{self.selected_stars_text(cfg)}' if by_star else ''))

        # 星级模式下六个星级开关都没勾时没有任何可打目标，不切御魂、不进游戏，直接退出
        if by_star and not any(self.star_flags(cfg).values()):
            logger.error('结束条件为「按星级」但六个星级开关（打1星 ~ 打6星鬼王）'
                         '一个都没勾选，没有可攻打的目标，任务直接结束。'
                         '请至少勾选一个星级，或把结束条件改为「按数量」。')
            self.set_next_run(task='ChaoGuiWang', success=False, finish=True)
            raise TaskEnd('ChaoGuiWang')

        # 全局要求 a：执行任务前切换御魂预设
        self.switch_soul_before_run(conf.switch_soul_config)

        # 步骤 1、2：庭院 → 活动图标 → 祸主降临
        # 失败时先逐个模板诊断界面并按流程恢复，恢复不回来才推送并结束
        if not self.enter_activity():
            logger.warning('步骤 1 未能进入活动，先做界面诊断恢复')
            if not self.diagnose_and_recover('进入活动入口'):
                self.push('超鬼王·任务完全失败',
                          f'庭院右侧未能进入超鬼王活动，界面诊断恢复也未成功，任务结束，'
                          f'请检查活动是否已开放。已击杀 {killed} 只。',
                          enabled=cfg.notify_task_failed)
                self.set_next_run(task='ChaoGuiWang', success=False, finish=True)
                raise TaskEnd('ChaoGuiWang')
        if not self.open_huozhu():
            logger.warning('步骤 2 未能进入祸主降临，先做界面诊断恢复')
            if not self.diagnose_and_recover('进入祸主降临界面'):
                self.push('超鬼王·任务完全失败',
                          f'未能进入「祸主降临」界面，界面诊断恢复也未成功，任务结束，'
                          f'请检查活动是否已开放。已击杀 {killed} 只。',
                          enabled=cfg.notify_task_failed)
                self.set_next_run(task='ChaoGuiWang', success=False, finish=True)
                raise TaskEnd('ChaoGuiWang')

        while 1:
            # 数量模式：达到设定只数即结束
            if not by_star and killed >= cfg.kill_count:
                self.push('超鬼王·打完所设置数量的鬼王',
                          f'已完成设定的 {cfg.kill_count} 只超鬼王，任务结束。'
                          f'共击杀 {killed} 只。',
                          enabled=cfg.notify_count_cleared)
                break

            # 只有面对「新鬼王」时，才需要等图标、判门票、判疲劳、读星级；
            # 挑战失败重打同一只鬼王时这些都跳过，直接再点挑战。
            if need_summon:
                logger.hr(f'超鬼王 已击杀 {killed} 只', 2)

                # 步骤 3：等待鬼面图标；若界面上已有可挑战的鬼王，直接开打
                demon_state = self.wait_demon_icon(cfg)
                if demon_state == 'NONE':
                    logger.warning('等待鬼面图标超时，先做界面诊断恢复')
                    timeouts += 1
                    recovered = self.diagnose_and_recover('等待鬼面图标')
                    if not recovered or timeouts >= cfg.max_consecutive_timeout:
                        self.push('超鬼王·任务完全失败',
                                  f'等待鬼面图标超时且界面恢复失败，任务结束。'
                                  f'已击杀 {killed} 只。',
                                  enabled=cfg.notify_task_failed)
                        break
                    continue

                if demon_state == 'EXISTING':
                    # 已有现成鬼王：不必召唤，直接点「挑战」；打完同样计一只
                    logger.info('已识别到现成鬼王，本轮跳过召唤，直接挑战')
                    need_summon = False
                else:
                    # 任意模式下，门票打空都直接结束（金色鬼面图标只在召唤界面出现）
                    if self.is_ticket_empty():
                        self.push('超鬼王·任务完成',
                                  f'所有鬼王门票已打完（图标显示金色鬼面），任务结束。'
                                  f'共击杀 {killed} 只。',
                                  enabled=cfg.notify_task_complete)
                        break

                # 步骤 4：疲劳度判定（严格「超过」阈值才停止）
                fatigue = self.get_fatigue()
                if fatigue is None:
                    self.push('超鬼王·超过疲劳阈值',
                              f'右上角疲劳度连续识别失败，为避免超疲劳，任务提前结束。'
                              f'已击杀 {killed} 只。',
                              enabled=cfg.notify_fatigue_limit)
                    break
                logger.info(f'当前疲劳度 {fatigue}/100，阈值 {cfg.fatigue_limit}')
                if fatigue > cfg.fatigue_limit:
                    self.push('超鬼王·超过疲劳阈值',
                              f'当前疲劳度 {fatigue}/100，超过设定阈值 {cfg.fatigue_limit}，'
                              f'任务结束。已击杀 {killed} 只。',
                              enabled=cfg.notify_fatigue_limit)
                    break

                # 步骤 5：星级判定。勾玉排只存在于召唤界面，
                # 现成鬼王的挑战界面上读不到星级，此时不做星级拦截、直接开打
                star = 0
                if demon_state == 'READY':
                    star = self.read_star_with_retry()
                    if star:
                        logger.info(f'识别到鬼王星级：{STAR_NAMES.get(star, star)}')
                    else:
                        logger.warning('未能识别鬼王星级，按允许处理继续战斗')
                    if by_star and star and not self.star_allowed(star, cfg):
                        self.push('超鬼王·打完所选星级鬼王',
                                  f'识别到 {STAR_NAMES.get(star, star)} 鬼王，不在勾选的星级范围内，'
                                  f'说明所选星级已打完，任务结束。'
                                  f'已击杀 {killed} 只。',
                                  enabled=cfg.notify_star_cleared)
                        break
                elif by_star:
                    logger.info('现成鬼王界面没有星级勾玉排，本轮不做星级筛选，直接挑战')

            # 步骤 6~9：打一轮
            result = self.fight_once(cfg, need_summon)
            if result == 'WIN':
                killed += 1
                defeats = 0
                timeouts = 0
                need_summon = True
                logger.info(f'挑战成功，已击杀超鬼王 {killed} 只')
                continue

            if result == 'LOSE':
                defeats += 1
                need_summon = False
                logger.warning(f'挑战失败，连续失败 {defeats}/{cfg.max_consecutive_defeat} 次，'
                               f'继续挑战同一只鬼王')
                if defeats >= cfg.max_consecutive_defeat:
                    self.push('超鬼王·连续挑战失败',
                              f'连续 {defeats} 次挑战失败，可能是御魂或阵容配置有问题，'
                              f'任务已停止。已击杀 {killed} 只。',
                              enabled=cfg.notify_defeat_limit)
                    break
                continue

            # TIMEOUT：界面卡住，逐个模板诊断并恢复
            stage = self.last_timeout_stage or '本轮战斗'
            logger.warning(f'{stage} 环节超时，先做界面诊断恢复')
            timeouts += 1
            recovered = self.diagnose_and_recover(stage)
            need_summon = True
            if not recovered or timeouts >= cfg.max_consecutive_timeout:
                self.push('超鬼王·任务完全失败',
                          f'{stage} 环节超时（含准备确认 {PREPARE_CONFIRM_TIMEOUT} 秒、'
                          f'战斗上限 {cfg.battle_timeout} 秒），界面恢复失败，任务结束。'
                          f'已击杀 {killed} 只。',
                          enabled=cfg.notify_task_failed)
                break

        logger.info(f'超鬼王任务结束，共击杀 {killed} 只')
        self.set_next_run(task='ChaoGuiWang', success=True, finish=True)
        raise TaskEnd('ChaoGuiWang')
