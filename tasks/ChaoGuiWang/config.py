# This Python file uses the following encoding: utf-8
"""超鬼王（祸主降临）任务配置。"""
from pydantic import Field

from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class ChaoGuiWangConfig(ConfigBase):
    # 需要击杀的超鬼王数量，完成一次「等待图标 → 结算点击」的完整循环计一只
    kill_count: int = Field(default=30, ge=1, le=999,
                            description='需要击杀的超鬼王数量，达到后推送并结束任务')
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
    # 连续超时保护：超时会推送后重试下一轮，达到该次数后终止，避免无限重试
    max_consecutive_timeout: int = Field(default=3, ge=1, le=20,
                                         description='连续超时达到该次数后推送并结束任务，避免无限重试')

    # ---------------- 消息推送开关 ----------------
    # 总开关 + 每个推送场景各自的开关，默认全部开启，保持原有推送行为。
    # 只想收关键通知时可以只留总开关和「任务完成」，把超时类推送逐个关掉。
    notify_enable: bool = Field(default=True,
                                description='消息推送总开关，关闭后本任务的所有推送都不再发送')
    notify_entry_failed: bool = Field(default=True,
                                      description='未找到庭院超鬼王活动入口时是否推送')
    notify_open_failed: bool = Field(default=True,
                                     description='进入「祸主降临」界面失败时是否推送')
    notify_wait_icon_timeout: bool = Field(default=True,
                                           description='第3步等待鬼面图标超时时是否推送')
    notify_fatigue_ocr_failed: bool = Field(default=True,
                                            description='右上角疲劳度识别失败时是否推送')
    notify_fatigue_limit: bool = Field(default=True,
                                       description='疲劳度超过阈值、任务停止时是否推送')
    notify_round_timeout: bool = Field(default=True,
                                       description='单轮召唤/挑战/准备/结算超时时是否推送')
    notify_task_complete: bool = Field(default=True,
                                       description='达到设定击杀数量、任务正常完成时是否推送')
    notify_task_aborted: bool = Field(default=True,
                                      description='连续超时等原因导致任务提前结束时是否推送')


class ChaoGuiWang(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    # 全局要求 a：执行任务前切换御魂预设
    switch_soul_config: SwitchSoulConfig = Field(default_factory=SwitchSoulConfig)
    chao_gui_wang_config: ChaoGuiWangConfig = Field(default_factory=ChaoGuiWangConfig)
