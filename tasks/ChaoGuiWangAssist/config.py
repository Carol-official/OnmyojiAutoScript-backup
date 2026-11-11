# This Python file uses the following encoding: utf-8
"""超鬼王协战任务配置。

两种模式互斥，只生效当前选中的一项：
  - 邀请模式：自己召唤鬼王后分享给好友，好友击杀即算一次邀请成功；
  - 协战模式：只打「好友发现」里的非公开鬼王，胜利结算一次即算一次协战成功。
"""
from enum import Enum

from pydantic import Field

from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class ChaoGuiWangAssistMode(str, Enum):
    """运行模式：邀请或协战，二者互斥，只生效当前选中的一项。"""
    INVITE = '邀请模式'
    ASSIST = '协战模式'


class ChaoGuiWangAssistConfig(ConfigBase):
    # ---------------- 模式 ----------------
    mode: ChaoGuiWangAssistMode = Field(
        default=ChaoGuiWangAssistMode.INVITE,
        description='运行模式：选「邀请模式」则召唤鬼王后分享给好友；'
                    '选「协战模式」则只打好友发现的非公开鬼王，未选中的那组设置自动失效')

    # ---------------- 邀请模式：召唤与分享的星级 ----------------
    # 勾选哪些星级就只召唤并分享哪些星级；识别到未勾选的星级说明该打的都打完了，任务结束。
    star_rank_1_4: bool = Field(default=True,
                                description='召唤并分享 1 至 4 星鬼王（邀请模式生效）')
    star_rank_5: bool = Field(default=False,
                              description='召唤并分享 5 星鬼王（邀请模式生效）')
    star_rank_6: bool = Field(default=False,
                              description='召唤并分享 6 星鬼王（邀请模式生效）')
    share_friend_name: str = Field(default='',
                                   description='分享目标好友名，须与分享弹窗中的名字完全一致；'
                                               '留空则每次选列表里的第一个好友')

    # ---------------- 等待节奏 ----------------
    # 邀请模式：分享后等好友击杀；协战模式：等好友发现新鬼王。两者共用以下两个参数。
    wait_interval: int = Field(default=10, ge=3, le=120,
                               description='每多少秒检查一次画面（邀请模式看鬼面是否重现，'
                                           '协战模式看好友是否发现新鬼王）')
    wait_timeout: int = Field(default=1800, ge=60, le=7200,
                              description='单次连续等待的最长秒数，默认 1800 秒（30 分钟）。'
                                          '邀请模式超时会推送提醒后继续等待；'
                                          '协战模式超时会推送「无协战鬼王」并结束任务')

    # ---------------- 协战模式：战斗保护 ----------------
    battle_timeout: int = Field(default=200, ge=30, le=600,
                                description='协战点击准备后等待战斗结束并结算的最长秒数')
    max_consecutive_defeat: int = Field(default=3, ge=1, le=20,
                                        description='连续协战失败达到该次数后停止任务并推送，'
                                                    '通常意味着御魂或阵容配置有问题')
    max_consecutive_timeout: int = Field(default=3, ge=1, le=20,
                                         description='连续超时达到该次数后结束任务，避免无限重试')

    # ---------------- 点击节奏 ----------------
    click_delay_min: float = Field(default=1.5, ge=0, le=10,
                                   description='每次点击前随机等待的下限秒数')
    click_delay_max: float = Field(default=3.0, ge=0, le=15,
                                   description='每次点击前随机等待的上限秒数，需不小于下限')

    # ---------------- 消息推送开关 ----------------
    notify_enable: bool = Field(default=True,
                                description='消息推送总开关，关闭后本任务的所有推送都不再发送')
    notify_task_complete: bool = Field(default=True,
                                       description='门票耗尽、任务正常完成时是否推送')
    notify_task_failed: bool = Field(default=True,
                                     description='找不到活动入口或界面恢复失败导致任务无法继续时是否推送')
    notify_star_cleared: bool = Field(default=True,
                                      description='识别到未勾选星级的鬼王、所选星级已打完时是否推送'
                                                  '（邀请模式生效）')
    notify_no_assist: bool = Field(default=True,
                                   description='协战模式等待超时仍无可用鬼王时是否推送')
    notify_invite_timeout: bool = Field(default=True,
                                        description='邀请模式分享后长时间未等到好友击杀时是否推送')
    notify_defeat_limit: bool = Field(default=True,
                                      description='连续协战失败达到上限、任务停止时是否推送')
    notify_summary: bool = Field(default=True,
                                 description='任务结束时是否推送一次汇总（含成功次数与总耗时）')


class ChaoGuiWangAssist(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    # 协战模式需要打斗，邀请模式不需要；两者都以开关控制，默认关闭
    switch_soul_config: SwitchSoulConfig = Field(default_factory=SwitchSoulConfig)
    chao_gui_wang_assist_config: ChaoGuiWangAssistConfig = Field(
        default_factory=ChaoGuiWangAssistConfig)
