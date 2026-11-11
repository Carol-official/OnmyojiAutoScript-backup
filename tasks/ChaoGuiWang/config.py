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
        description='结束条件：选「按数量」则打到设定只数结束，'
                    '选「按星级」则按下面六个星级开关结束，未选中的那组设置自动失效')
    # 数量模式
    kill_count: int = Field(default=30, ge=1, le=999,
                            description='需要击杀的超鬼王数量（按数量模式生效）')

    # ---------------- 星级模式 ----------------
    # 勾选哪些星级就只打哪些星级；识别到未勾选的星级说明该打的都打完了，任务结束。
    # 六个星级各自独立开关；默认勾选 1~4 星，与拆分前的「打1至4星鬼王」行为一致。
    star_rank_1: bool = Field(default=True,
                              description='打 1 星鬼王（按星级模式生效）')
    star_rank_2: bool = Field(default=True,
                              description='打 2 星鬼王（按星级模式生效）')
    star_rank_3: bool = Field(default=True,
                              description='打 3 星鬼王（按星级模式生效）')
    star_rank_4: bool = Field(default=True,
                              description='打 4 星鬼王（按星级模式生效）')
    star_rank_5: bool = Field(default=False,
                              description='打 5 星鬼王（按星级模式生效）')
    star_rank_6: bool = Field(default=False,
                              description='打 6 星鬼王（按星级模式生效）')

    @model_validator(mode='before')
    @classmethod
    def migrate_star_rank_1_4(cls, data):
        """旧配置只有「打1至4星鬼王」一个开关，加载时按它的值展开成 1~4 星四个开关。

        只在旧字段存在、且四个新字段一个都没写的情况下生效，不会覆盖用户的新设置。
        """
        if not isinstance(data, dict):
            return data
        new_keys = ('star_rank_1', 'star_rank_2', 'star_rank_3', 'star_rank_4')
        if 'star_rank_1_4' in data and not any(key in data for key in new_keys):
            old = bool(data['star_rank_1_4'])
            for key in new_keys:
                data[key] = old
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
