# This Python file uses the following encoding: utf-8
"""超鬼王（祸主降临）任务配置。"""
from enum import Enum

from pydantic import Field, model_validator

from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class ChaoGuiWangTarget(str, Enum):
    """结束条件：按数量或按星级，二者互斥，只生效当前选中的一项。"""
    COUNT = '按数量'
    STAR = '按星级'


class ChaoGuiWangConfig(ConfigBase):
    # ---------------- 结束条件 ----------------
    target_mode: ChaoGuiWangTarget = Field(
        default=ChaoGuiWangTarget.COUNT,
        description='结束条件：选「按数量」则打到设定只数结束；'
                    '选「按星级」则按下面「是否攻打」开关判定所选星级是否打完，'
                    '未选中的那组设置自动失效')
    # 数量模式
    kill_count: int = Field(default=30, ge=1, le=999,
                            description='需要击杀的超鬼王数量（按数量模式生效）')

    # ---------------- 每个星级的处理开关 ----------------
    # 「是否攻打」替代旧的 star_rank_N：识别到未勾选的星级就不打这只鬼王。
    # 「是否强追」默认全部关闭（强力追击默认关闭，需手动打开）；只有勾了才会在
    # 点「挑战」前把界面切成强追模式，未勾选的星级一律切回普追。
    # 字段名带 cgw_ 前缀，与协战任务的开关在全局扁平 i18n 命名空间里互不冲突。
    #
    # 布局：客户端（oasx）按字段声明顺序逐条铺开，所以这里「按星级分组」排列，
    # 同一星级的攻打/强追连在一起，一屏能看全。
    # 这里刻意不写 description —— oasx 会把 description 渲染成标题下面的一行说明，
    # 每条开关因此变成两行高，12 条就撑满两屏；去掉后每条只剩一行短标题。
    # 显示用的标题在 assets/i18n/zh-CN.json（键名 = 字段名）：
    # 每个星级的第一条写成「N 星鬼王：攻打」充当分组表头，同星级的其余开关
    # 只留缩进（全角空格）对齐到「攻打」那一列，视觉上是一块一块的。
    cgw_fight_star_1: bool = True
    cgw_pursuit_star_1: bool = False
    cgw_fight_star_2: bool = True
    cgw_pursuit_star_2: bool = False
    cgw_fight_star_3: bool = True
    cgw_pursuit_star_3: bool = False
    cgw_fight_star_4: bool = True
    cgw_pursuit_star_4: bool = False
    cgw_fight_star_5: bool = False
    cgw_pursuit_star_5: bool = False
    cgw_fight_star_6: bool = False
    cgw_pursuit_star_6: bool = False

    @model_validator(mode='before')
    @classmethod
    def migrate_old_star_flags(cls, data):
        """旧配置的星级开关迁移到 cgw_fight_star_1~6，避免用户已有设置丢失。

        兼容三代旧字段名：star_rank_1_4 / star_rank_5 / star_rank_6（最早的三开关）、
        star_rank_1~6（拆分后的六开关）。只在新字段一个都没写时才迁移，
        不会覆盖用户按新界面保存过的设置；迁移后把旧键删掉。
        """
        if not isinstance(data, dict):
            return data
        fight = tuple(f'cgw_fight_star_{rank}' for rank in range(1, 7))
        old_six = tuple(f'star_rank_{rank}' for rank in range(1, 7))
        old_three = ('star_rank_1_4', 'star_rank_5', 'star_rank_6')
        if not any(key in data for key in fight):
            # star_rank_5 / star_rank_6 在两个时代里同名，必须先认「三开关时代」
            # 独有的 star_rank_1_4，否则三开关配置会被六开关分支截获，
            # 把明明关掉的 1~4 星按默认值恢复成开启
            if 'star_rank_1_4' in data:
                v = bool(data['star_rank_1_4'])
                values = [v, v, v, v,
                          bool(data.get('star_rank_5', False)),
                          bool(data.get('star_rank_6', False))]
            elif any(key in data for key in old_six):
                values = [bool(data.get(key, rank <= 4))
                          for rank, key in enumerate(old_six, 1)]
            else:
                values = None
            if values is not None:
                for key, value in zip(fight, values):
                    data[key] = value
        for key in old_six + old_three:
            data.pop(key, None)
        return data

    # ---------------- 运行节奏与保护 ----------------
    # 疲劳度上限，游戏内 100 为满；当前值「超过」该配置才停止
    fatigue_limit: int = Field(default=90, ge=1, le=100,
                               description='疲劳度阈值（100为满），当前疲劳度超过该值时推送并停止任务')
    # 第3步：等待「自己发现」区域鬼面图标出现
    wait_icon_timeout: int = Field(default=40, ge=5, le=300,
                                   description='第3步等待鬼面图标出现的最长秒数')
    wait_icon_interval: int = Field(default=5, ge=1, le=30,
                                    description='第3步每次检查鬼面图标的间隔秒数')
    # 第7步：点击准备后等待鬼王打完并出现结算界面
    battle_timeout: int = Field(default=200, ge=30, le=600,
                                description='点击准备后等待战斗结束并结算的最长秒数')
    # 连续挑战失败（出现「失败」结算界面）保护
    max_consecutive_defeat: int = Field(default=3, ge=1, le=20,
                                        description='连续挑战失败达到该次数后停止任务并推送，'
                                                    '通常意味着御魂或阵容配置有问题')
    # 连续超时保护：超时后会诊断界面并重试，达到该次数后终止，避免无限重试
    max_consecutive_timeout: int = Field(default=3, ge=1, le=20,
                                         description='连续超时达到该次数后结束任务，避免无限重试')

    # ---------------- 消息推送开关 ----------------
    # 只保留六类关键通知，每类可单独关闭；所有通知都会带上已击杀数量。
    notify_enable: bool = Field(default=True,
                                description='消息推送总开关，关闭后本任务的所有推送都不再发送')
    notify_task_complete: bool = Field(default=True,
                                       description='门票耗尽、任务正常完成时是否推送')
    notify_task_failed: bool = Field(default=True,
                                     description='找不到活动入口或界面恢复失败导致任务无法继续时是否推送')
    notify_star_cleared: bool = Field(default=True,
                                      description='打完所选星级鬼王时是否推送（按星级模式生效）')
    notify_count_cleared: bool = Field(default=True,
                                       description='打完所设置数量的鬼王时是否推送（按数量模式生效）')
    notify_fatigue_limit: bool = Field(default=True,
                                       description='疲劳度超过阈值、任务停止时是否推送')
    notify_defeat_limit: bool = Field(default=True,
                                      description='连续挑战失败达到上限、任务停止时是否推送')


class ChaoGuiWang(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    # 全局要求 a：执行任务前切换御魂预设
    switch_soul_config: SwitchSoulConfig = Field(default_factory=SwitchSoulConfig)
    chao_gui_wang_config: ChaoGuiWangConfig = Field(default_factory=ChaoGuiWangConfig)
