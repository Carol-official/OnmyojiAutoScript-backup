# This Python file uses the following encoding: utf-8
"""超鬼王协战任务配置。

两种模式互斥，只生效当前选中的一项：
  - 邀请模式：自己召唤鬼王后分享给好友，好友击杀即算一次邀请成功；
  - 协战模式：只打「好友发现」里的非公开鬼王，胜利结算一次即算一次协战成功。
"""
from enum import Enum

from pydantic import Field, model_validator

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

    # ---------------- 每个星级的处理开关 ----------------
    # 协战模式：先判星级 →「是否攻打」决定是否打 →「是否强追」决定是否切强追。
    # 邀请模式：「是否邀请」决定是否召唤并分享这只鬼王。
    # 「是否强追」默认全部关闭（强力追击默认关闭，需手动打开）。
    # 字段名带 cgwa_ 前缀，与祸主降临任务的开关在全局扁平 i18n 命名空间里互不冲突。
    #
    # 布局：客户端（oasx）按字段声明顺序逐条铺开，所以这里「按星级分组」排列，
    # 同一星级的攻打/邀请/强追连在一起，18 条能一屏看全。
    # 这里刻意不写 description —— oasx 会把 description 渲染成标题下面的一行说明，
    # 每条开关因此变成两行高，18 条要占两屏多；去掉后每条只剩一行短标题。
    # 显示用的标题在 assets/i18n/zh-CN.json（键名 = 字段名）：
    # 每个星级的第一条写成「N 星鬼王：攻打」充当分组表头，同星级的邀请/强追
    # 只留缩进（全角空格）对齐到「攻打」那一列，视觉上是一块一块的。
    cgwa_fight_star_1: bool = True
    cgwa_invite_star_1: bool = True
    cgwa_pursuit_star_1: bool = False
    cgwa_fight_star_2: bool = True
    cgwa_invite_star_2: bool = True
    cgwa_pursuit_star_2: bool = False
    cgwa_fight_star_3: bool = True
    cgwa_invite_star_3: bool = True
    cgwa_pursuit_star_3: bool = False
    cgwa_fight_star_4: bool = True
    cgwa_invite_star_4: bool = True
    cgwa_pursuit_star_4: bool = False
    cgwa_fight_star_5: bool = False
    cgwa_invite_star_5: bool = False
    cgwa_pursuit_star_5: bool = False
    cgwa_fight_star_6: bool = False
    cgwa_invite_star_6: bool = False
    cgwa_pursuit_star_6: bool = False

    @model_validator(mode='before')
    @classmethod
    def migrate_old_star_flags(cls, data):
        """旧 invite_star_1~6 / star_rank_1_4 等迁移到新的按星级开关。

        旧配置只有一组「邀请N星鬼王」，新配置拆成「是否邀请 / 是否攻打」两组：
        旧值先写入 cgwa_invite_star_1~6，若 cgwa_fight_star_1~6 一个都没写，
        再用同一组值初始化协战模式的「是否攻打」，保持原来的打/不打范围。
        只在新字段缺失时迁移，不覆盖用户按新界面保存过的设置；迁移后删掉旧键。
        """
        if not isinstance(data, dict):
            return data
        fight = tuple(f'cgwa_fight_star_{rank}' for rank in range(1, 7))
        invite = tuple(f'cgwa_invite_star_{rank}' for rank in range(1, 7))
        old_invite = tuple(f'invite_star_{rank}' for rank in range(1, 7))
        old_six = tuple(f'star_rank_{rank}' for rank in range(1, 7))
        old_three = ('star_rank_1_4', 'star_rank_5', 'star_rank_6')
        default = [True, True, True, True, False, False]

        values = None
        if not any(key in data for key in invite):
            # star_rank_5 / star_rank_6 在「三开关」与「六开关」两代配置里同名，
            # 必须先认三开关独有的 star_rank_1_4，否则旧配置会被六开关分支截获
            if any(key in data for key in old_invite):
                values = [bool(data.get(key, default[rank - 1]))
                          for rank, key in enumerate(old_invite, 1)]
            elif 'star_rank_1_4' in data:
                v = bool(data['star_rank_1_4'])
                values = [v, v, v, v,
                          bool(data.get('star_rank_5', False)),
                          bool(data.get('star_rank_6', False))]
            elif any(key in data for key in old_six):
                values = [bool(data.get(key, rank <= 4))
                          for rank, key in enumerate(old_six, 1)]
            if values is not None:
                for key, value in zip(invite, values):
                    data[key] = value
        if values is None:
            values = [bool(data.get(key, default[rank - 1]))
                      for rank, key in enumerate(invite, 1)]
        if not any(key in data for key in fight):
            for key, value in zip(fight, values):
                data[key] = value
        for key in old_invite + old_six + old_three:
            data.pop(key, None)
        return data
    share_friend_name: str = Field(default='',
                                   description='分享目标好友名，须与分享弹窗中的名字完全一致。'
                                               '邀请模式只邀请这一位好友，不会改邀请列表里的其他人；'
                                               '留空时任务直接结束，不做兜底选择')

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
    notify_friend_missing: bool = Field(default=True,
                                        description='邀请模式在分享弹窗里找不到指定好友、'
                                                    '为不误邀请其他人而结束任务时是否推送')
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
