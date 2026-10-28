"""离线验证冒险时长选择的两个判据（不连设备、不改游戏状态）。

用法：python tools/verify_adventure_duration_offline.py [截图目录]
      截图目录默认是仓库同级的 runs/（真机跑出来的截图与控件树快照）。

需要的四个文件（两两成对）：
  _prep.png        + dump_prep.xml        诗和远方(2小时) 选中
  _prep_short.png  + dump_prep_short.xml  附近走走(45秒)  选中

验证内容与 select_duration() 依赖的完全一致：
  1) 选项卡片靠整屏 OCR 文字定位（'附近走走' / '诗和远方'）
  2) 选中态靠控件树 content-desc 判别（两段简介互斥）
"""
import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.ocr import find_text, ocr_fullscreen          # noqa: E402
from src.locators import LOCATORS                      # noqa: E402

RUNS = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO.parent / 'runs'
CASES = [
    ('_prep.png', '2小时'),
    ('_prep_short.png', '45秒'),
]


def load(name: str) -> np.ndarray:
    img = cv2.imread(str(RUNS / name))
    if img is None:
        raise SystemExit(f'读不到截图: {RUNS / name}')
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def hit_desc(xml_text: str, key: str) -> bool:
    """等价于 xpath //*[contains(@content-desc, key)] 的离线判定。"""
    import re
    return any(key in m for m in re.findall(r'content-desc="([^"]*)"', xml_text))


def main() -> int:
    short_xpath = LOCATORS['adventure_short_desc']['xpath'][0]
    long_xpath = LOCATORS['adventure_long_desc']['xpath'][0]
    short_key = short_xpath.split('"')[1]
    long_key = long_xpath.split('"')[1]
    print(f'短选项简介关键字: {short_key!r}')
    print(f'长选项简介关键字: {long_key!r}\n')

    ok = True
    for name, selected in CASES:
        img = load(name)
        h, w = img.shape[:2]
        xml_path = RUNS / ('dump_prep_short.xml' if 'short' in name else 'dump_prep.xml')
        xml_text = xml_path.read_text(encoding='utf-8', errors='replace')

        print(f'=== {name}  尺寸 {w}x{h}  预期选中: {selected} ===')

        # 1) OCR 找两个选项（两张图上两张卡片都可见，都应能命中）
        for loc_name, label in (('adventure_short', '附近走走'),
                                ('adventure_long', '诗和远方')):
            words = LOCATORS[loc_name]['ocr']
            found = None
            for target in words:
                found = find_text(ocr_fullscreen(img), target)
                if found:
                    break
            if found:
                print(f'  OCR {label:<6} -> ({found[0]}, {found[1]}) score={found[2]:.3f}'
                      f'  相对x={found[0] / w:.2f}')
            else:
                print(f'  OCR {label:<6} -> 未命中  <== 失败')
                ok = False

        # 2) 选中态判别：两段简介必须一真一假
        s_hit = hit_desc(xml_text, short_key)
        l_hit = hit_desc(xml_text, long_key)
        print(f'  简介命中: 短={s_hit}  长={l_hit}')
        if s_hit == l_hit:
            print('  两种简介同时命中或同时未命中，无法判别选中态  <== 失败')
            ok = False
        else:
            actual = '45秒' if s_hit else '2小时'
            print(f'  判别结果: {actual}  {"正确" if actual == selected else "错误  <== 失败"}')
            if actual != selected:
                ok = False
        print()

    print('结论:', '全部通过' if ok else '存在失败项')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
