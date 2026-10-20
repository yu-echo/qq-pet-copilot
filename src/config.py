"""配置加载：config.yaml，以及 adb 路径定位。

路径规划（兼顾 PyInstaller 打包）：
- APP_ROOT：可写数据根目录。开发时是项目根目录；打包后是 exe 所在目录。
  config.yaml、runs/（进度、日志）都放在这里。
- RESOURCE_ROOT：只读资源根目录。打包后是 PyInstaller 解压目录（sys._MEIPASS），
  scrcpy-win64/ 等随包资源从这里读；APP_ROOT 下存在同名资源时优先使用，
  方便用户不重新打包直接替换。
"""
from __future__ import annotations

import os
import shutil
import sys
import copy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml


def _app_root() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = _app_root()
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", APP_ROOT))
from .profiles import resolve_profile

PROFILE_ID, DATA_ROOT = resolve_profile(APP_ROOT)
PROJECT_ROOT = DATA_ROOT  # 兼容数据文件引用；源码和共享资源仍使用 APP_ROOT

CONFIG_FILE = DATA_ROOT / "config.yaml"


def resource_path(rel: str | Path) -> Path:
    """定位资源。

    源码运行：项目根下（resources/ 等）存在则用，否则随包资源；
    打包运行：exe 旁的 runs/（可写数据目录）下放同名资源即可覆盖随包资源
    （如 runs/resources/scrcpy-win64/），无需重新打包。
    """
    rel = Path(rel)
    if rel.is_absolute():
        return rel
    if getattr(sys, "frozen", False):
        local = APP_ROOT / "runs" / rel
        return local if local.exists() else RESOURCE_ROOT / rel
    local = APP_ROOT / rel
    return local if local.exists() else RESOURCE_ROOT / rel


# Windows 上 adb 的常见安装位置
_COMMON_ADB_PATHS = [
    r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe",
    r"%USERPROFILE%\AppData\Local\Android\Sdk\platform-tools\adb.exe",
    r"C:\platform-tools\adb.exe",
    r"D:\platform-tools\adb.exe",
    r"C:\Android\platform-tools\adb.exe",
    r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe",
]


@dataclass
class AdbConfig:
    path: str = ""
    device_serial: str = ""




@dataclass
class ControlConfig:
    # 控制方案：injectInputEvent（uiautomator2 d.touch，走 u2 server 事件注入，默认）/
    # minitouch（openstf minitouch socket 本地直发触摸事件，模拟器上 d.click 不可靠时更稳）
    method: str = "injectInputEvent"


@dataclass
class SchoolConfig:
    # 属性点课程：力量 / 智力 / 魅力
    attribute: str = "力量"
    # 每天学习次数上限，0 为不限
    times_per_day: int = 0


@dataclass
class WorkConfig:
    # 打工地点（OCR 文字匹配），如：风铃旅社 / 星尘魔法塔 / 彩虹画室
    location: str = "风铃旅社"
    # 打工时长选择：10分钟（select_box_1）/ 45分钟（select_box_2）/ 2小时（select_box_3），
    # 打工与雇佣好友共用
    duration: str = "10分钟"
    # 每天打工次数上限，0 为不限
    times_per_day: int = 0
    # 已不再使用：旧流程"下滑找雇佣按钮"已移除（当前页没有雇佣按钮时直接关闭面板开工），
    # 保留字段仅为兼容已有 config.yaml
    employ_scroll_limit: int = 5


@dataclass
class ScheduleConfig:
    # 金币阈值：金币 >= 该值优先学习，低于则先打工赚够再学习
    coin_threshold: int = 2000
    # 学习工作时长上限（小时）：学习/打工按学园/打工时长结算累计，
    # 累计时长 >= 上限后今天不再学习只打工；0 = 不限
    daily_hour_limit: int = 8
    # 旧版字段（仅兼容老 config.yaml + 首次运行迁移用，不再参与调度、不进设置页）：
    # school_factor / work_factor 按"每节/每次的分钟数"把老进度次数换算成已学习/工作时长；
    # daily_point_limit 已废弃，仅保留让老配置能正常解析
    school_factor: int = 20
    work_factor: int = 45
    daily_point_limit: int = 0
    # 进行中状态（上课/打工/冒险/被雇佣）的统一检查间隔（秒）
    check_interval: int = 8
    # 鼓励次数：在学习/打工进行中页面快速点击"鼓励宠物"按钮的次数，0 为不鼓励
    # （非阻塞调度在登记 pending 离开进行中页面前就地点击；结算页没有该按钮）
    encourage_times: int = 10
    # 鼓励概率（0-100）：进行中页面每次发现"鼓励宠物"按钮时，掷中才点鼓励。
    # 100 = 每次都点（与加这个字段之前的行为完全一致），0 = 从不鼓励
    encourage_probability: int = 100
    # 主页面检测次数：连续这么多次识别不到主页面标志（金币胶囊）才允许点 back
    # （主页面点 back 会退出游戏；默认 1 = 识别不到立即点 back，即原逻辑）
    main_page_checks: int = 1
    # 返回方式：系统返回（Android 返回键，默认）/ 返回图标（定位游戏内 back 按钮点击）
    back_method: str = "系统返回"


@dataclass
class AdventureConfig:
    # 每天冒险次数，0 为不冒险
    times_per_day: int = 1
    # 冒险调度时间（HH:MM），到达后优先冒险
    start_time: str = "08:00"
    # 冒险时长选择：45秒（附近走走）/ 2小时（诗和远方）；默认选短的，
    # 2 小时会长时间占住设备且按次计数不划算
    duration: str = "45秒"
    # 开始冒险后检测冒险详情框：出现"天色不对"就点召回->确认召回，计入一次冒险。
    # 仅在冒险时长为 45 秒时生效（2 小时的冒险不触发天色不对召回）
    skip_bad_weather: bool = True
    # 已废弃：一轮连跑的冒险次数（每轮只跑一次，计划表接管每日次数）。
    # 保留字段仅为兼容已有 config.yaml
    batch: int = 12


@dataclass
class CareConfig:
    # 护理方式：ocr检测（读宠物状态，低于阈值手动喂食/洗澡）/ 一键护理（直接点主页面的一键护理按钮）
    method: str = "ocr检测"
    # 自动喂食：体力低于阈值时喂食；关掉则不判断体力，只做清洁
    auto_feed: bool = True
    # 自动洗护：清洁低于阈值时洗澡；关掉则不判断清洁，只做喂食
    auto_bathe: bool = True
    # 体力阈值：低于则喂食到达标
    energy_threshold: int = 60
    # 清洁阈值：低于则洗澡到达标
    clean_threshold: int = 60
    # 护理间隔（秒）：距上次护理检查至少间隔这么久才再次检查
    interval_seconds: int = 60


@dataclass
class VisitConfig:
    # 每天踩踩次数，0 为不踩
    times_per_day: int = 10
    # 踩踩调度时间（HH:MM），到达后开始处理
    start_time: str = "00:01"


@dataclass
class PkConfig:
    # 每天 PK 次数，0 为不 PK；每个好友可 PK 3 次，打完自动切换下一个好友
    times_per_day: int = 15
    # PK 调度时间（HH:MM），到达后开始处理
    start_time: str = "00:01"


@dataclass
class FriendCareConfig:
    # 启用好友护理开关（与经验日常合并：访问第一个好友，检测到可加经验就一键护理）
    enabled: bool = False
    # 已废弃：好友护理时间段（不再按时间段调度，全天按间隔巡检）。
    # 保留字段仅为兼容已有 config.yaml
    time_range: str = "14:00-19:30"
    # 已废弃：护理好友名称（不再检测名字，自动访问第一个好友）。
    # 保留字段仅为兼容已有 config.yaml
    friend_name: str = ""
    # 已废弃：护理好友方式（统一为一键护理加经验）。
    # 保留字段仅为兼容已有 config.yaml
    method: str = "ocr检测"
    # 调度间隔（秒）：每次调度只做一次巡检，距上次巡检至少间隔这么久才再次调度
    interval_seconds: int = 60


@dataclass
class HireFriendConfig:
    # 雇佣好友开关
    enabled: bool = False
    # 已废弃：雇佣好友时间段（不再按时间段调度，全天可用）。
    # 保留字段仅为兼容已有 config.yaml
    time_range: str = "19:31-23:59"
    # 已废弃：调度间隔（不再限制间隔，打工时每次都雇佣）。
    # 保留字段仅为兼容已有 config.yaml
    interval_seconds: int = 5
    # 已废弃：雇佣好友名称（不再检测名字，自动选择第一个好友）。
    # 保留字段仅为兼容已有 config.yaml
    friend_name: str = ""
    # 已废弃：每天雇佣好友次数（不再按次数限制，打工时每次都雇佣）。
    # 保留字段仅为兼容已有 config.yaml
    times_per_day: int = 8


# 任务队列调度的任务键（tasks.order 里可配置的任务名）
TASK_KEYS = ('care', 'adventure', 'visit', 'pk', 'hire_friend', 'friend_care',
             'school', 'work', 'svip')
# 主任务组：冒险/学习/打工/雇佣好友互斥（共用"出门-进行中"一条线，不能同时做），
# 由 TaskQueueRunner 按 tasks.main_order 统一调度
MAIN_TASK_KEYS = ('adventure', 'school', 'hire_friend', 'work')


@dataclass
class TaskItemConfig:
    # 单个任务的调度设置（config.yaml 的 tasks.<任务名> 段）
    enabled: bool = True
    # trigger: interval=按间隔秒数 / daily=按每日时间点列表（到点打开一个执行窗口）
    trigger: str = "interval"
    interval_seconds: int = 60
    daily_times: list = field(default_factory=list)  # HH:MM 列表
    # 允许执行的时间窗（HH:MM:SS-HH:MM:SS）
    enabled_time_range: str = "00:00:00-23:59:59"
    # 成功/失败后距下次执行的最小间隔（秒）
    success_interval: int = 60
    failure_interval: int = 1800


@dataclass
class TasksConfig:
    # 执行顺序（> 分隔，越靠前越优先）；不在 order 里的任务不调度
    order: str = "svip>care>school>friend_care>hire_friend>adventure>visit>pk>work"
    # 主任务组（冒险/学习/打工/雇佣好友，互斥）组内优先级（> 分隔，越靠前越优先）；
    # 没列出的主任务按默认顺序兜底排最后
    main_order: str = "school>hire_friend>adventure>work"
    # 所有任务统一的失败重试间隔（秒）：设置页"任务失败重试间隔"，
    # 覆盖各任务的 failure_interval（只保留这一个入口，避免界面改不到/漏改）
    failure_interval: int = 1800
    care: TaskItemConfig = field(default_factory=TaskItemConfig)
    adventure: TaskItemConfig = field(default_factory=TaskItemConfig)
    visit: TaskItemConfig = field(default_factory=TaskItemConfig)
    pk: TaskItemConfig = field(default_factory=TaskItemConfig)
    hire_friend: TaskItemConfig = field(default_factory=TaskItemConfig)
    friend_care: TaskItemConfig = field(default_factory=TaskItemConfig)
    school: TaskItemConfig = field(default_factory=TaskItemConfig)
    work: TaskItemConfig = field(default_factory=TaskItemConfig)
    # 每日领取 QQ SVIP 会员礼包（默认每日时间点触发一次）
    svip: TaskItemConfig = field(default_factory=lambda: TaskItemConfig(
        trigger="daily", daily_times=["09:05"], success_interval=60))


# 每日活动计划：可参与排队的活动、完成方式、学习可选属性
PLAN_ACTIVITIES = ('school', 'work', 'adventure')
PLAN_MODES = ('count', 'duration')          # 按次数 / 按时长
PLAN_ATTRIBUTES = ('力量', '智力', '魅力')


@dataclass
class PlanItemConfig:
    """每日活动计划的一条（config.yaml 的 plan[] 元素）。

    与旧的 school/work/adventure 分段参数并存：计划表是"做什么、做多少"，
    旧分段参数仍是"怎么做"（打工地点、单次时长、冒险时长等）。计划条目里
    留空的活动专属字段回落到旧分段参数，避免两处都要配。
    """
    activity: str = 'school'        # school / work / adventure
    mode: str = 'count'             # count=按次数完成 / duration=按时长完成
    count: int = 1                  # mode=count：今天做够几次算完成（>=1）
    hours: float = 1.0              # mode=duration：今天累计做够几小时算完成（>0）
    attribute: str = ''             # 学习：力量/智力/魅力（空=用 school.attribute）
    work_location: str = ''         # 打工：地点（空=用 work.location）
    work_duration: str = ''         # 打工：单次时长（空=用 work.duration）
    adventure_duration: str = ''    # 冒险：单次时长 45秒/2小时（空=用 adventure.duration）


def _normalize_plan_item(item: dict) -> dict:
    """清洗一条计划：未知键丢弃、非法值回退，保证构造 dataclass 不抛异常。"""
    valid = {k: v for k, v in (item or {}).items()
             if k in PlanItemConfig.__dataclass_fields__}
    activity = str(valid.get('activity') or 'school').strip()
    if activity not in PLAN_ACTIVITIES:
        activity = 'school'
    mode = str(valid.get('mode') or 'count').strip()
    if mode not in PLAN_MODES:
        mode = 'count'
    try:
        count = int(valid.get('count', 1))
    except (TypeError, ValueError):
        count = 1
    try:
        hours = float(valid.get('hours', 1.0))
    except (TypeError, ValueError):
        hours = 1.0
    return {
        'activity': activity,
        'mode': mode,
        'count': max(1, count),
        'hours': round(max(0.1, hours), 2),
        'attribute': str(valid.get('attribute') or '').strip(),
        'work_location': str(valid.get('work_location') or '').strip(),
        'work_duration': str(valid.get('work_duration') or '').strip(),
        'adventure_duration': str(valid.get('adventure_duration') or '').strip(),
    }


def parse_plan(raw_plan, school: 'SchoolConfig', work: 'WorkConfig',
               adventure: 'AdventureConfig') -> 'PlanConfig':
    """解析 config.yaml 的 plan 段；没有该段时按旧的分段参数生成默认三条。

    旧配置里 school/work.times_per_day=0 表示"不限次数"，计划表没有"不限"
    这个选项，统一按 1 次迁移（用户可在界面上直接改），并在日志里说明。
    """
    items: list[PlanItemConfig] = []
    if isinstance(raw_plan, list) and raw_plan:
        for entry in raw_plan:
            if isinstance(entry, dict):
                items.append(PlanItemConfig(**_normalize_plan_item(entry)))
    if not items:
        items = [
            PlanItemConfig(activity='school', mode='count',
                           count=max(1, int(school.times_per_day or 1)),
                           attribute=school.attribute),
            PlanItemConfig(activity='work', mode='count',
                           count=max(1, int(work.times_per_day or 1)),
                           work_location=work.location,
                           work_duration=work.duration),
            PlanItemConfig(activity='adventure', mode='count',
                           count=max(1, int(adventure.times_per_day or 1)),
                           adventure_duration=adventure.duration),
        ]
    return PlanConfig(items=items)


@dataclass
class PlanConfig:
    # 自动控制总开关：关闭后不按每日计划执行（手动任务不受影响）
    enabled: bool = True
    # 每日活动计划（按列表顺序执行，做完一条再进下一条）
    items: list = field(default_factory=list)


@dataclass
class RunnerConfig:
    # 调度引擎：task_queue（任务队列调度，默认）/ legacy（老主循环调度）
    engine: str = "task_queue"
    # 成长福袋巡检间隔（秒）：每次巡检会出门去好友家，别配得太密
    moneybag_interval_seconds: int = 300


@dataclass
class RecoverConfig:
    # 异常恢复方式：重启设备（adb reboot，彻底）/ 重启游戏（只强停并重开 QQ，快）
    method: str = "重启游戏"



@dataclass
class EmployedConfig:
    # 被雇佣开关：开启后全天按检查间隔出门检查是否被雇佣中
    enabled: bool = False
    # 已废弃：被雇佣时间段（不再按时间段调度，全天检查）。
    # 保留字段仅为兼容已有 config.yaml
    time_range: str = "19:31-23:59"
    # 被雇佣检查间隔（秒）：距上次检查至少间隔这么久才再次出门检查
    interval_seconds: int = 60
    # 被雇佣后处理：等到25/75（分成比例到 雇佣者<=25% 被雇佣者>=75% 才召回，收益最高）/
    # 等到25/75（小于45min）（同左，但面板剩余时间 >45 分钟立即召回，<=45 分钟继续等到25/75）/
    # 立刻召回（进被雇佣面板直接点"现在召回"）
    action: str = "等到25/75（小于45min）"


@dataclass
class NotifyConfig:
    # 场景多次重试仍失败时的告警：Windows 桌面 Toast 通知
    win_toast: bool = True
    # OnePush 推送配置（YAML，支持多行），如 {provider: bark, key: xxx}；留空不推送
    onepush_config: str = ""


@dataclass
class GuiConfig:
    # 界面主题：跟随系统 / 深色 / 浅色（仅 GUI 用，调度器不读）
    theme: str = "跟随系统"
    # 画面镜像开关（主页工具栏，开关状态持久化；仅 GUI 用）
    mirror: bool = True
    # 仅右上角 × 的行为；配置切换和 Alt+F4 仍正常退出。
    close_action: str = "关闭程序"


@dataclass
class Config:
    adb: AdbConfig = field(default_factory=AdbConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    gui: GuiConfig = field(default_factory=GuiConfig)
    school: SchoolConfig = field(default_factory=SchoolConfig)
    work: WorkConfig = field(default_factory=WorkConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    adventure: AdventureConfig = field(default_factory=AdventureConfig)
    care: CareConfig = field(default_factory=CareConfig)
    visit: VisitConfig = field(default_factory=VisitConfig)
    pk: PkConfig = field(default_factory=PkConfig)
    friend_care: FriendCareConfig = field(default_factory=FriendCareConfig)
    hire_friend: HireFriendConfig = field(default_factory=HireFriendConfig)
    employed: EmployedConfig = field(default_factory=EmployedConfig)
    runner: RunnerConfig = field(default_factory=RunnerConfig)
    tasks: TasksConfig = field(default_factory=TasksConfig)
    plan: PlanConfig = field(default_factory=PlanConfig)
    recover: RecoverConfig = field(default_factory=RecoverConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)


def find_adb(configured_path: str = "") -> str:
    """按 配置路径 -> 随包 adb -> PATH -> 常见目录 的顺序定位 adb。"""
    candidates = []
    if configured_path:
        # 相对路径优先取 APP_ROOT（用户自定义），其次随包资源
        p = Path(configured_path)
        candidates.append(str(p if p.is_absolute() else resource_path(p)))
    # 随包 scrcpy 自带的 adb 兜底：配置路径失效时仍可用（如 exe 连同旧 config.yaml
    # 拷到别的电脑——配置里是指向原机器的路径/旧相对路径，本机靠 PATH 兜住没暴露）
    bundled = str(resource_path('resources/scrcpy-win64/adb.exe'))
    if bundled not in candidates:
        candidates.append(bundled)
    which = shutil.which("adb")
    if which:
        candidates.append(which)
    for raw in _COMMON_ADB_PATHS:
        candidates.append(os.path.expandvars(raw))

    for path in candidates:
        if path and Path(path).is_file():
            return str(Path(path))
    raise FileNotFoundError(
        "找不到 adb。请安装 platform-tools，并在 config.yaml 的 adb.path 中填写 adb.exe 完整路径。"
    )


@lru_cache(maxsize=4)
def _read_config_cached(path: str, mtime_ns: int, size: int, inode: int) -> dict:
    """按文件版本缓存解析结果；每次调用仍返回独立配置，热更新不受影响。"""
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def load_config(config_path: str | Path | None = None) -> Config:
    path = Path(config_path) if config_path else CONFIG_FILE
    if not path.is_file():
        # 打包后首次运行：把随包默认配置复制到 exe 旁，便于用户修改
        for name in ('config.yaml', 'config.example.yaml'):
            bundled = RESOURCE_ROOT / name
            if bundled.is_file() and bundled.resolve() != path.resolve():
                shutil.copy(bundled, path)
                break
    stat = path.stat()
    raw = copy.deepcopy(_read_config_cached(str(path.resolve()), stat.st_mtime_ns,
                                            stat.st_size, stat.st_ino))
    raw_tasks = raw.get("tasks", {}) or {}
    tasks = TasksConfig()
    if raw_tasks.get("order"):
        tasks.order = str(raw_tasks["order"])
    if raw_tasks.get("main_order"):
        tasks.main_order = str(raw_tasks["main_order"])
    for key in TASK_KEYS:
        item = raw_tasks.get(key)
        if isinstance(item, dict):
            # 过滤掉未知键，防止 YAML 里多写的键让 dataclass 构造报错
            valid = {k: v for k, v in item.items() if k in TaskItemConfig.__dataclass_fields__}
            setattr(tasks, key, TaskItemConfig(**valid))
    # 统一失败重试间隔：tasks.failure_interval（设置页"任务失败重试间隔"）覆盖所有任务，
    # 不再单独配置各任务的 failure_interval
    if raw_tasks.get("failure_interval") is not None:
        tasks.failure_interval = int(raw_tasks["failure_interval"])
    for key in TASK_KEYS:
        getattr(tasks, key).failure_interval = tasks.failure_interval
    # 分段参数先落地成局部变量：计划表的默认值要读它们，Config(...) 里也复用同一份
    school = SchoolConfig(**raw.get("school", {}))
    work = WorkConfig(**raw.get("work", {}))
    adventure = AdventureConfig(**raw.get("adventure", {}))
    plan = parse_plan(raw.get("plan"), school, work, adventure)
    if isinstance(raw.get("plan_enabled"), bool):
        plan.enabled = raw["plan_enabled"]
    return Config(
        adb=AdbConfig(**raw.get("adb", {})),
        control=ControlConfig(
            **{k: v for k, v in (raw.get("control", {}) or {}).items()
               if k in ControlConfig.__dataclass_fields__}),
        gui=GuiConfig(**{k: v for k, v in (raw.get("gui", {}) or {}).items()
                         if k in GuiConfig.__dataclass_fields__}),
        school=school,
        work=work,
        schedule=ScheduleConfig(**raw.get("schedule", {})),
        adventure=adventure,
        care=CareConfig(**raw.get("care", {})),
        visit=VisitConfig(**raw.get("visit", {})),
        pk=PkConfig(**raw.get("pk", {})),
        friend_care=FriendCareConfig(**raw.get("friend_care", {})),
        hire_friend=HireFriendConfig(
            **{k: v for k, v in (raw.get("hire_friend", {}) or {}).items()
               if k in HireFriendConfig.__dataclass_fields__}),
        employed=EmployedConfig(**raw.get("employed", {})),
        runner=RunnerConfig(**{k: v for k, v in (raw.get("runner", {}) or {}).items()
                               if k in RunnerConfig.__dataclass_fields__}),
        tasks=tasks,
        plan=plan,
        recover=RecoverConfig(**{k: v for k, v in raw.get("recover", {}).items() if k in RecoverConfig.__dataclass_fields__}),
        notify=NotifyConfig(**raw.get("notify", {})),
    )
