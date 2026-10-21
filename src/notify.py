"""告警通知：场景多次重试仍失败时多渠道通知用户（参考 qq-farm-copilot）。

渠道（config.yaml 的 notify 段配置，可叠加，任一成功即算送达）：
- Windows Toast 桌面通知：notify.win_toast: true（需 winotify）
- PushPlus 推送：notify.pushplus_token 填 token（留空则读环境变量
  PUSHPLUS_TOKEN）；token 属敏感信息，日志只记「已配置/未配置」，绝不打印
- OnePush 推送：notify.onepush_config 填 YAML（设置页是多行输入框），
  支持 Bark / PushPlus / Server酱 / Telegram / SMTP / 自定义 webhook 等
  onepush 提供方，例如：
    onepush_config: "{provider: bark, key: 你的Key}"
  各提供方参数教程（ALAS wiki 中文文档）：
  https://github.com/LmeSzinc/AzurLaneAutoScript/wiki/Onepush-configuration-%5BCN%5D

告警时可附当前手机屏幕截图（image_path）：Toast 用作图标，
OnePush 放进 image_path 字段（是否展示取决于提供方）。
发送失败只记日志不抛异常——告警本身不能再把调度器弄崩。
"""
from __future__ import annotations

import os
import sys

from .config import NotifyConfig, load_config
from .progress import log

TITLE = '[QQ宠物助手告警]'


def send_alert(reason: str, image_path: str | None = None) -> bool:
    """按 notify 配置发送告警，返回是否有渠道发送成功。"""
    try:
        cfg = load_config().notify
    except Exception as e:
        # 配置坏了也要尽量报出来：按默认配置（Windows Toast）发
        log(f'告警通知: 读取配置失败（{e}），按默认配置发送')
        cfg = NotifyConfig()
    try:
        sent = False
        if cfg.win_toast:
            sent = _send_windows_toast(reason, image_path) or sent
        if str(cfg.onepush_config).strip():
            sent = _send_onepush(str(cfg.onepush_config), reason, image_path) or sent
        token = _pushplus_token(cfg)
        # onepush_config 里已经写了 pushplus 时不重复推（否则一次告警收两条）
        if token and _onepush_provider(str(cfg.onepush_config)) != 'pushplus':
            sent = _onepush_notify(
                {'provider': 'pushplus', 'token': token}, reason, image_path) or sent
        if not sent:
            log('告警通知: 未发送成功（未配置渠道或发送失败，详见上方日志）')
        return sent
    except Exception as e:
        # 告警本身绝不能再把调度器弄崩：任何异常都记日志后返回失败
        log(f'告警通知: 发送过程异常: {e}')
        return False


def _send_windows_toast(reason: str, image_path: str | None = None) -> bool:
    """发送 Windows Toast 通知。"""
    if not sys.platform.startswith('win'):
        log('告警通知: 非 Windows 平台，跳过 Toast')
        return False
    try:
        from winotify import Notification
    except ImportError:
        log('告警通知: 未安装 winotify，跳过 Windows Toast')
        return False
    except Exception as e:
        log(f'告警通知: winotify 导入失败: {e}，跳过 Windows Toast')
        return False
    icon = str(image_path or '')
    if not icon or not os.path.exists(icon):
        icon = ''
    try:
        toast = Notification(
            app_id='QQPetCopilot', title=TITLE, msg=str(reason), duration='long',
            **({'icon': icon} if icon else {}),
        )
        toast.show()
        log('告警通知: Windows Toast 推送成功')
        return True
    except Exception as e:
        log(f'告警通知: Windows Toast 发送失败: {e}')
        return False


def _pushplus_token(cfg: NotifyConfig) -> str:
    """取 PushPlus token：配置优先，其次环境变量 PUSHPLUS_TOKEN；都不打印。"""
    return str(cfg.pushplus_token or '').strip() or os.environ.get('PUSHPLUS_TOKEN', '').strip()


def _onepush_provider(config_text: str) -> str:
    """尽力读出 onepush_config 里的 provider（小写），解析不了返回空串。"""
    import yaml

    try:
        data = yaml.safe_load(str(config_text or '').strip())
    except Exception:
        return ''
    if not isinstance(data, dict):
        return ''
    return str(data.get('provider') or '').strip().lower()


def _send_onepush(config_text: str, reason: str, image_path: str | None = None) -> bool:
    """发送 OnePush 通知。config_text 为单行 YAML（flow 格式），必须含 provider。"""
    import yaml

    try:
        cfg = yaml.safe_load(config_text.strip())
    except yaml.YAMLError as e:
        log(f'告警通知: OnePush 配置 YAML 解析失败: {e}')
        return False
    if not isinstance(cfg, dict):
        log('告警通知: OnePush 配置不是字典，跳过推送')
        return False
    return _onepush_notify(dict(cfg), reason, image_path)


def _onepush_notify(cfg: dict, reason: str, image_path: str | None = None) -> bool:
    """按已解析的 onepush 配置发送（cfg 里的 provider 会被取走）。"""
    cfg = dict(cfg)
    provider = str(cfg.pop('provider', '') or '').strip()
    if not provider:
        log('告警通知: OnePush 未配置 provider，跳过推送')
        return False
    secret = str(cfg.get('token') or cfg.get('key') or '')
    try:
        from onepush import get_notifier
        from onepush.providers.custom import Custom
    except ImportError:
        log('告警通知: 未安装 onepush，跳过 OnePush 推送')
        return False
    except Exception as e:
        log(f'告警通知: onepush 导入失败: {e}，跳过 OnePush 推送')
        return False
    try:
        notifier = get_notifier(provider)
        payload: dict = dict(cfg)
        payload['title'] = TITLE
        payload['content'] = str(reason)
        if image_path and os.path.exists(image_path):
            payload['image_path'] = image_path
        if isinstance(notifier, Custom):
            # 自定义 webhook：默认 JSON POST，title/content 固定塞进 data
            if str(payload.get('method', 'post')).lower() == 'post':
                payload['datatype'] = 'json'
            data = payload.get('data')
            if not isinstance(data, dict):
                data = {}
            data['title'] = payload['title']
            data['content'] = payload['content']
            payload['data'] = data
        response = notifier.notify(**payload)
        status_code = int(getattr(response, 'status_code', 200) or 200)
        if status_code != 200:
            log(f'告警通知: OnePush 推送失败，状态码={status_code}')
            return False
        if provider == 'pushplus':
            # PushPlus 失败也回 HTTP 200（例如 token 无效 code=903），必须看响应体的 code
            try:
                body = response.json()
            except Exception:
                body = None
            if isinstance(body, dict):
                code = int(body.get('code', 200) or 0)
                if code != 200:
                    msg = str(body.get('msg') or '')
                    if secret:
                        msg = msg.replace(secret, '***')
                    log(f'告警通知: PushPlus 推送失败，code={code} msg={msg}')
                    return False
        log(f'告警通知: OnePush 推送成功（{provider}）')
        return True
    except Exception as e:
        detail = str(e).strip() or repr(e)
        if secret:
            # 异常文本里万一带上 token/key，先抹掉再进日志
            detail = detail.replace(secret, '***')
        log(f'告警通知: OnePush 推送失败（{provider}）: {type(e).__name__}: {detail}')
        return False
