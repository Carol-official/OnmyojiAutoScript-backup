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


class ChaoGuiWang(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    # 全局要求 a：执行任务前切换御魂预设
    switch_soul_config: SwitchSoulConfig = Field(default_factory=SwitchSoulConfig)
    chao_gui_wang_config: ChaoGuiWangConfig = Field(default_factory=ChaoGuiWangConfig)
