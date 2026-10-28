"""应用版本信息。

此文件可由 tools/write_version.py 在打包流程中自动更新（release 工作流按 tag 写入）。
"""

APP_VERSION = '1.5'
# 指向本仓库：更新检查只看这里发的 Release；上游仍是 hu181b（原项目）
APP_GITHUB_REPO = 'yu-echo/qq-pet-copilot'
APP_RELEASES_URL = f'https://github.com/{APP_GITHUB_REPO}/releases/latest'
