# -*- coding: utf-8 -*-
"""rerun_final_docs.py —— 一键重生成 `data/plans/final/` 的全部交付物。

为什么要有它（2026-09-20）
--------------------------
用户口径：「**每次出新 db 的时候必须带一份专门的伤害循环文档**」。
一旦模型口径变了（例如「装备授予的 WPS 入池」），**已落盘的产物不会自己更新** ——
实测踩过：三份 `CYCLE_*` 里只有一份是修复前生成的，另两份恰好是修复后生成的，
「部分陈旧」最难发现（见 `docs/pitfalls.md` 同名专题）。

⇒ 把「哪份产物由哪条命令生成」写成表，模型改完跑一次本脚本，**全部重新落盘**。

用法
----
    python tools/rerun_final_docs.py                # 全部重生成
    python tools/rerun_final_docs.py --only werewolf,wolf_nightblade_fast
    python tools/rerun_final_docs.py --check        # 只重算到临时文件并 diff（不覆盖）

两种产物（每个 BD **必带两份**）
    `CYCLE_*`  = 伤害循环档案（`tools/plan_cycle.py`）—— 数字**怎么来的**
    `REPORT_*` = 方案体检（`tools/plan_audit.py --md`）—— 数字**对不对**
"""
from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, 'data', 'plans', 'final')
PY = sys.executable

# (产物名, 方案 JSON, --arch, --alloc（B 口径才有）, 敌方档)
#
# 口径说明：
#   A 口径 = `data/plans/lv73_A/`（**不注入加点**，真实存档技能 + 形态装备，可落地）★主口径
#   B 口径 = `data/plans/lv73_B/`（`GD_SKILL_JSON` 整体替换核心加点）
#      ⚠ 2026-09-20 重设：B 口径**暂时退役** —— 自动加点器会给双持形态塞 24 点进
#      `wpattack1/2/3`，武器池 ΣW 撑过 100 ⇒ 默认攻击权重归零 ⇒ 主输出一次不出现
#      （`docs/pitfalls.md` #53，实测 −50%）。修好加点器之前不用它做结论。
JOBS = [
    # ---- Sam 可选的 8 个 class10 形态（A 口径 = 真实存档，可落地）----
    ('werewolf',              'data/plans/lv73_A/arch_werewolf.json',
     'werewolf', '', 'm3955'),
    ('raven_nightblade',      'data/plans/lv73_A/arch_raven_nightblade.json',
     'raven_nightblade', '', 'm3955'),
    ('wolf_nightblade_fast',  'data/plans/lv73_A/arch_wolf_nightblade_fast.json',
     'wolf_nightblade_fast', '', 'm3955'),
    ('wolf_nightblade',       'data/plans/lv73_A/arch_wolf_nightblade.json',
     'wolf_nightblade', '', 'm3955'),
    ('wereraven',             'data/plans/lv73_A/arch_wereraven.json',
     'wereraven', '', 'm3955'),
    ('human',                 'data/plans/lv73_A/arch_human.json',
     'human', '', 'm3955'),
    ('berserker_nightblade',  'data/plans/lv73_A/arch_berserker_nightblade.json',
     'berserker_nightblade', '', 'm3955'),
    ('avalanche',             'data/plans/lv73_A/arch_avalanche.json',
     'avalanche', '', 'm3955'),
    # ---- ★ 主穿刺形态（`GD_OBJ=pierce` 的路线，2026-09-20 新增）----
    #   它的装备方案不在 `lv73_A/`（那是 12 个「合计目标」形态的普查），
    #   而在 `data/plans/lv73_pierce/`（用 `GD_OBJ=pierce` 单独搜的）。
    #   `--alloc` 指向**终版技能集**（含新星座），否则循环文档只反映「只换装备」。
    ('pierce_lv73', 'data/plans/lv73_pierce/arch_wolf_nightblade_pierce.json',
     'wolf_nightblade_pierce', 'data/scratch/skills_opt_pierce.json', 'm3955'),
    # ---- ★ v8（2026-09-21）：**模型修正后的合计最优** ----
    #   口径变更 = `docs/pitfalls.md` #61（WPS **武器闸门** + **降敌 DA 接入 PTH**）。
    #   方案 = 装备 2/14 槽（头部「巴尔迪尔的面具」/ 勋章「冰原巨狼盾徽」，
    #   旧勋章授予的「混乱打击」是双持触发不了的盾牌战技）+ 星座 32→34 点
    #   + 战争兵器 1→3。`--alloc` 指向终版技能集（**含新星座**，否则循环只反映「只换装备」）。
    ('v8_lv73', 'data/plans/lv73_v8/arch_wolf_nightblade_fast.json',
     'wolf_nightblade_fast', 'data/scratch/skills_opt_v8.json', 'm3955'),
    # ---- ★ opt_lv73（2026-09-21）：**主轴普查后的合计最优完整 BD** ----    #   装备 = `GD_OBJ=total` + `--extreme` 搜出的 **9/14 槽**换装方案
    #     （副手换成带 138% 护甲穿透的「林恩·瓦尔戈斯的切肉斧」，物理整块并入穿刺）；
    #   星座 = `tune_devotion --realloc` 整池重排 **34 点**
    #     （退 海鳗/抉择之地/乌龟 6 点 → 加 狐狸 4 + 猫头鹰 4 + 豺狼 3）；
    #   技能 = `tune_skills --mode swap` 终版（战争兵器 +11 / 跃击 +5 / 血性狂热 −6 …）。
    #   `--alloc` 必须指向 **`alloc_opt.json`**（由 `skill_opt.json` 抽出的纯加点），
    #   否则 `load_char` 会把元数据键（`mode`/`arch`）当成技能等级而崩。
    ('opt_lv73', 'data/plans/pivot/x_total.json',
     'wolf_nightblade_fast', 'data/plans/pivot/alloc_opt.json', 'm3955'),
    # ---- ★ opt_lv73_legal（2026-09-21）：**修正装备位合法性后重搜的版本** ----
    #   起因：`opt_lv73` 那份装备里有 **3 处非法装备位**（游戏会静默摘掉）——
    #     主手/副手镶了护甲组件「弹性铠甲片」、勋章镶了戒指/项链附魔「炼狱粉尘」。
    #   修了 6 个洞（`docs/pitfalls.md` **#68**）后用**同一套参数**重搜：
    #     `GD_OBJ=total gd auto Sam --extreme --archetype wolf_nightblade_fast`
    #   合法性：`tools/plan_legal.py` **0 处非法** ✓
    #   数字（`gd auto` 同 harness）：合法现状 208,753 → **216,114（+3.5%）**
    #   （旧 `x_total` 的 210,248 里有 ~1,495 是**非法位虚增**的假收益）。
    #   ★ 加点用 **`alloc_legal.json`**（= 装备合法版 + `tune_skills` 的 `跃击 6→8`）——
    #     它是**纯技能/精通**文件（**不含星座**）：`GD_SKILL_JSON` 注入的星座是
    #     **求并集不是替换**，带进去会被静默叠加（`docs/pitfalls.md` **#69**）。
    #     星座侧维持存档原样（`tune_devotion --realloc` 已加不劣化兜底 ⇒ 本轮 `adopted=false`）。
    #   数字：面板 130,427 ｜ 含命中 144,432 ｜ **含减抗 284,980**（×1.973）｜ 合法性 0 处非法 ✓
    ('opt_lv73_legal', 'data/plans/pivot/x_total_fix2.json',
     'wolf_nightblade_fast', 'data/plans/pivot/alloc_legal.json', 'm3955'),
    # ---- ★ fix3_lv73（2026-09-21 16:07 落档）：**模型修正后重搜 + 全套落档**的当前 BD ----
    #   起因：`opt_lv73_legal` 的装备是在「装备授予常驻技能入表」修复**之前**搜的。
    #   重搜 = `GD_OBJ=total gd auto Sam --extreme --archetype wolf_nightblade_fast`（326 s）
    #   ⇒ 装备 **11 槽**（双「蝎尾狮长剑」/ 苦难星座 / 苦难根等），`plan_legal` 0 处非法 ✓
    #   技能：`leap1 8→16`（max=16）、`bloodborne1 5→2`、`werewolf2 11→8`（净 +2）
    #   星座：退螳螂根 → 补苦难根（仍 **40/40**）；技能点计数同步扣到 0
    #   `--alloc` 传空 ⇒ 直接用**存档加点**（落档后存档即目标态）。
    #   ⚠ **形态漂移知会**：新武器丢了旧副手的「138% 物理→穿刺」⇒ 构成从
    #     「穿刺 59.9 + 流血 36.5」变成「毒酸DoT 27.3 + 流血 23.6 + 穿刺 18.2 + 物理 17.1
    #     + 毒酸 10.4」；带护甲真值怪 `m3955` 复核仍 **+9.16%**（非护甲口径 +8.91%）。
    #   数字：面板 165,646.5 ｜ 实战 192,405.4 ｜ **含减抗 274,047**（指纹 `5436cb14d160`）
    ('fix3_lv73', 'data/plans/pivot/x_total_fix3.json',
     'wolf_nightblade_fast', '', 'm3955'),
    # ---- ★ lokarr_lv73（2026-09-21 16:54 落档）：**换靶到罗卡（Lokarr）后的最优 BD** ----
    #   靶子 = **`m1281` = 罗卡（Lokarr）**：SuperBoss、护甲 1029、抗性 82–92%（社区 DPS 基准王）。
    #   命令 = `GD_ENEMY_PROFILE=m1281 GD_OBJ=total gd auto Sam --extreme
    #           --archetype wolf_nightblade_fast`（7 分 43 秒）⇒ 装备 **4 槽**、0 处非法。
    #   ★ 技能 / 星座 / 属性**完全不动**（罗卡口径下 `--realloc` adopted=False、技能 1-1 净 −163）。
    #   ★★ 换靶会把形态**钉回穿刺+流血**：流血 23.6→31.0、穿刺 18.2→25.7、
    #      物理 17.1→11.1、毒酸DoT 27.3→15.0；并带来全 10 型「最多 −30%」C 类减抗。
    #   ⚠ **换靶不是白拿**：打罗卡 **+24.0%**，但打低抗杂兵（默认等级池档）**−9.37%**。
    #   ⚠ 本作业的 `--enemy` 用 **`m1281`**（其余作业用 `m3955`）⇒ **数字不可跨作业相减**。
    #   数字：面板 137,719.3 ｜ 实战 159,831.0 ｜ **含减抗 248,083**（指纹 `2e8dc006a055`）
    ('lokarr_lv73', 'data/plans/pivot/x_lokarr.json',
     'wolf_nightblade_fast', '', 'm1281'),
    # ---- B 口径（注入加点）—— **2026-09-20 退役，暂不产出** ----
    #   理由：自动加点器会给双持形态塞 24 点进 `wpattack1/2/3`，武器池 ΣW 撑过 100
    #   ⇒ `默认攻击权重 = max(0, 100−ΣW) = 0` ⇒ 主输出（野性利爪）**一次都不出现**
    #   （`docs/pitfalls.md` #53，实测 −50%）。修好加点器之前，B 口径的数字**不能做结论**，
    #   所以既不跑普查也不出交付物。修好后把下面一行恢复即可：
    # ('wolf_nightblade_fast_B', 'data/plans/lv73_B/arch_wolf_nightblade_fast.json',
    #  'wolf_nightblade_fast', 'data/scratch/alloc73_wolf_nightblade_fast.json', 'm3955'),
]


def _run(cmd, cwd=HERE):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')


def _strip_noise(text):
    """剥掉报告正文**之前**的标定日志。

    ⚠ 实测：`plan_audit --md` 走 stdout 之前，`load_char` 会打印
      `  [武器套] alt1 → …`（每次评估一行，一次生成能刷 18 行）——
      直接落盘会把报告头部污染成日志。正文的固定首行是
      `**技能来源合法性**…`（或 `# `），从这里切。
    """
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith('**技能来源合法性**') or ln.startswith('# '):
            return '\n'.join(lines[i:]).rstrip() + '\n'
    return text


def _diff(old_path, new_text):
    if not os.path.isfile(old_path):
        return ['（原文件不存在）']
    with open(old_path, encoding='utf-8') as fh:
        old = fh.read()
    d = list(difflib.unified_diff(old.splitlines(), new_text.splitlines(),
                                  'old', 'new', lineterm='', n=0))
    return [x for x in d if x.startswith(('+', '-')) and not x.startswith(('+++', '---'))][:6]


def main() -> int:
    ap = argparse.ArgumentParser(description='重生成 data/plans/final/ 的交付物')
    ap.add_argument('--only', default='', help='只做这些（逗号分隔，如 werewolf）')
    ap.add_argument('--check', action='store_true',
                    help='不覆盖：重算到临时文件并打印差异行数')
    a = ap.parse_args()
    only = {x.strip() for x in a.only.split(',') if x.strip()}

    os.makedirs(OUT, exist_ok=True)
    bad = 0
    for name, plan, arch, alloc, enemy in JOBS:
        if only and name not in only:
            continue
        if not os.path.isfile(os.path.join(HERE, plan)):
            print('  ✗ %-26s 方案缺失 %s' % (name, plan))
            bad += 1
            continue

        # ---- CYCLE（伤害循环档案）
        cyc = os.path.join(OUT, 'CYCLE_%s.md' % name)
        cmd = [PY, 'tools/plan_cycle.py', 'Sam', plan, '--arch', arch,
               '--enemy', enemy]
        if alloc:
            cmd += ['--alloc', alloc]
        if a.check:
            r = _run(cmd)
            dl = _diff(cyc, r.stdout)
            print('  %-26s CYCLE  %s' % (name, ('差异 %d 行 %s' % (len(dl), dl[:2]))
                                         if dl else '逐位一致'))
            continue
        cmd += ['--out', os.path.relpath(cyc, HERE).replace(os.sep, '/')]
        r = _run(cmd)
        if r.returncode != 0:
            print('  ✗ %-26s CYCLE 失败：%s' % (name, (r.stderr or '')[-160:]))
            bad += 1
        else:
            print('  ✓ %-26s CYCLE  %s' % (name, (r.stderr or '').strip().splitlines()[-2:]))

        # ---- REPORT（方案体检）
        rep = os.path.join(OUT, 'REPORT_%s.md' % name)
        rc = [PY, 'tools/plan_audit.py', 'Sam', plan, '--arch', arch,
              '--enemy', enemy, '--md']
        if alloc:
            rc += ['--alloc', alloc]
        rr = _run(rc)
        if rr.returncode != 0:
            print('  ✗ %-26s REPORT 失败：%s' % (name, (rr.stderr or '')[-160:]))
            bad += 1
            continue
        with open(rep, 'w', encoding='utf-8') as fh:
            fh.write(_strip_noise(rr.stdout))
        print('  ✓ %-26s REPORT %d 行' % (name, _strip_noise(rr.stdout).count('\n') + 1))

    print('\n%d 处失败' % bad if bad else '\n交付物已全部重新落盘')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
