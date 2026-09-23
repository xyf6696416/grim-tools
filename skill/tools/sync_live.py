# -*- coding: utf-8 -*-
"""把 `tools/` 下的保命副本同步回 `gd/` 活路径。

    python tools/sync_live.py            # 检查（只报告，不改）
    python tools/sync_live.py --apply    # 差异处用副本覆盖活文件
    python tools/sync_live.py --capture  # 反向：活文件 -> 副本（改完代码后刷新副本）

为什么需要它
------------
活文件会被**迁移脚本**打回旧版：`tools/migrate_from_archive.py` 的 `FILES` 表
从旧归档搬 `gd_req.py` 等基座文件，重跑一次就把成熟版覆盖掉（实测丢 881 行，
`gd/req.py` 从 507 行退回 253 行的估算版）。`tools/` 下的副本不在迁移管辖内，
因此**每次长时间操作前后**跑一次本脚本，并在「恢复 → 执行」同一条命令里串联。

被覆盖前，活文件会先另存为 `tools/_reverted/<名字>.<md5前8位>`，便于事后比对。

★ `--capture` 是配套义务：改完 `PAIRS` 里任一活文件后必须跑一次，
  否则副本变旧，下次迁移/抢修就会把新代码打回去。
"""
from __future__ import annotations
import argparse
import hashlib
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (活文件, 保命副本)
#
# ★ 怎么判断该进表还是该写迁移的 patch 步骤：
#   「能在**旧基座上用几处字符串替换**表达的小改动」→ 迁移的 patch 步骤；
#   「**重写级**（新增维度/新参数/新函数，几十上百行）」→ 必须进本表。
#   判据不是「文件大不大」，而是「改动能不能用稳定锚点的替换表达」——
#   实测 `gd/dps.py` 与 `gd/opt.py` 的积累改动已分别到 131 / 458 行
#   （减抗维度、伤害代理标定、`load_char` 的构建覆盖），
#   写成一百多处细粒度 patch 既脆弱又不可读 → 改由本表整体覆盖。
#   ⚠ 迁移里那几条针对 dps/opt 的老 patch 步骤（10/11/15/16/17/20/21）
#     因此**已被本表取代**（它们仍会作用于基座，但随后会被覆盖）——
#     新增改动请改这里，别再往那些步骤上叠。
PAIRS = [
    ('gd/req.py', 'tools/gd_req_official.py'),
    ('gd/rotation.py', 'tools/gd_rotation_patched.py'),
    ('gd/reqfit.py', 'tools/gd_reqfit_patched.py'),
    ('gd/paths.py', 'tools/gd_paths_patched.py'),
    ('gd/alloc.py', 'tools/gd_alloc_patched.py'),
    ('gd/build.py', 'tools/gd_build_patched.py'),
    ('gd/dps.py', 'tools/gd_dps_patched.py'),
    ('gd/opt.py', 'tools/gd_opt_patched.py'),
    ('gd/rr.py', 'tools/gd_rr_patched.py'),
    ('gd/save/backup.py', 'tools/gd_save_backup_patched.py'),
    ('gd/save/verify.py', 'tools/gd_save_verify_patched.py'),
    ('gd/save/patch.py', 'tools/gd_save_patch_patched.py'),
    # ★ `gd/save/write.py` 与 `gd/save/skill.py` **都在迁移的 `FILES` 里**
    #   （源 `gd_write.py` / `gd_skill.py`）⇒ 不登记的话，重跑迁移会把改动静默打回。
    #   2026-09-21 修的是**星点 `devotion_level`**：新增记录要设（`write.blank_skill`）、
    #   重新点亮也要设（`skill.apply_plan`）—— 游戏按它判定星点是否点亮。
    ('gd/save/write.py', 'tools/gd_save_write_patched.py'),
    ('gd/save/skill.py', 'tools/gd_save_skill_patched.py'),
    # ---- 伤害模型 v2 新增模块（2026-09-19）----
    # ★ 它们**不在**迁移的 `FILES` 里（旧归档没有这三个文件），所以不会被迁移打回；
    #   登记进来是为了另一件事：迁移的步骤 24「权威覆盖」会按本表把副本搬回活路径，
    #   于是**从零重建 skill** 时这三个模块也能自动就位，而不是悄悄缺掉。
    #   顺带 `tools/selftest.py` 的 [13] 组会守住「活文件 == 副本」这条不变量。
    ('gd/combat.py', 'tools/gd_combat_patched.py'),
    ('gd/dmg.py', 'tools/gd_dmg_patched.py'),
    ('gd/enemy.py', 'tools/gd_enemy_patched.py'),
    # `gd/cli.py` 同样是**手写**文件（归档里没有），迁移不会碰它；
    # 登记是为了让「从零重建」也带上它 —— 子命令的**原样透传**表在这里，
    # 少了它 `gd dps Sam --enemy-difficulty normal` 会被 argparse 打乱参数顺序。
    ('gd/cli.py', 'tools/gd_cli_patched.py'),
    # `gd/dbr.py` 也是**手写** shim（归档里没有；`REL` 表只是把旧 `gd_dbr` 的
    # 相对导入改名，不产生文件）。2026-09-20 给它加了第 5 步「技能字段补洞」
    # （从 `data/calc_mastery_skills.json` 回填 `skillChanceWeight`），
    # 不登记的话从零重建会得到一个**没有补洞**的版本 ⇒ WPS 又变回默认攻击。
    ('gd/dbr.py', 'tools/gd_dbr_patched.py'),
    # ★ `gd/planreport.py` **在**迁移的 `FILES` 里（源 `gd_plan_report.py`）⇒
    #   不登记的话，重跑迁移会把 2026-09-20 加的 `--audit`（把真实 DPS / 属性重排
    #   体检段并进报告）与更早的「含武器」修正**静默打回**。
    ('gd/planreport.py', 'tools/gd_planreport_patched.py'),
    # ★ `gd/skillprov.py` 是 **A 方案（技能来源合法性）** 的新模块（2026-09-20）：
    #   判断形态的 `root_skills` / `core_skills` 里那些 `records/skills/itemskills*`
    #   记录有没有**真被已装备物品授予**（或等级够不够），把「fangs 完美姿态
    #   lv71 不可构建却排第一」这类静默错误挡在模型之外。
    #   它不在迁移的 `FILES` 里 ⇒ 重跑迁移不会打回；登记是为了让**从零重建**
    #   也能自动就位（同样的理由见上面 combat/dmg/enemy/cli/dbr）。
    ('gd/skillprov.py', 'tools/gd_skillprov_patched.py'),
    # ★ `gd/procs.py`（物品技能**触发关系** + 装备授予 WPS 入池）与
    #   `gd/dmgcycle.py`（伤害循环文档生成器）是 2026-09-20 新增的手写模块，
    #   同样**不在**迁移 `FILES` 里 ⇒ 从零重建会整块缺掉
    #   （`tools/plan_cycle.py` 直接报 ImportError）。登记后可由迁移终步自动就位。
    ('gd/procs.py', 'tools/gd_procs_patched.py'),
    ('gd/dmgcycle.py', 'tools/gd_dmgcycle_patched.py'),
    # ★ `gd/resaudit.py`（抗性来源分解，2026-09-20 新增）是**手写**模块，
    #   不在迁移 `FILES` 里；但它被 `gd/planreport.py`（§二）与
    #   `tools/plan_audit.py`（交付物 `REPORT_*.md`）同时 import ⇒
    #   从零重建时缺了它，两条报告链路会**同时**退化（分解段消失或报错）。
    ('gd/resaudit.py', 'tools/gd_resaudit_patched.py'),
]


def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()


def capture():
    """反向同步：活文件 -> 保命副本。

    改完受管文件**必须**跑一次，否则 `migrate_from_archive.py` 的「权威覆盖」
    终步（步骤 23）会拿旧副本把新代码打回去。
    """
    bad = 0
    for live, bak in PAIRS:
        lp, bp = os.path.join(ROOT, live), os.path.join(ROOT, bak)
        if not os.path.isfile(lp):
            print('  ✗ 活文件缺失 %s' % live)
            bad += 1
            continue
        if os.path.isfile(bp) and md5(lp) == md5(bp):
            print('  = %-22s 已一致' % live)
            continue
        shutil.copy2(lp, bp)
        print('  ↑ %-22s -> %s' % (live, bak))
    if bad:
        print('\n%d 处活文件缺失，副本未更新。' % bad)
        return 1
    print('\n副本已刷新。')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真正覆盖（默认只报告）')
    ap.add_argument('--capture', action='store_true',
                    help='反向：活文件 -> 副本（改完代码后刷新保命副本）')
    a = ap.parse_args()

    if a.capture:
        return capture()

    bad = 0
    for live, bak in PAIRS:
        lp, bp = os.path.join(ROOT, live), os.path.join(ROOT, bak)
        if not os.path.isfile(bp):
            print('  ✗ 缺副本   %-22s <- %s' % (live, bak))
            bad += 1
            continue
        if not os.path.isfile(lp):
            state = '活文件不存在'
            same = False
        else:
            same = md5(lp) == md5(bp)
            state = '一致' if same else '★ 不一致(疑似被回滚)'
        print('  %s %-22s %s' % ('✓' if same else '✗', live, state))
        if same:
            continue
        bad += 1
        if not a.apply:
            continue
        if os.path.isfile(lp):
            rd = os.path.join(ROOT, 'tools', '_reverted')
            os.makedirs(rd, exist_ok=True)
            dst = os.path.join(rd, os.path.basename(live) + '.' + md5(lp)[:8])
            shutil.copy2(lp, dst)
            print('       已留档被回滚版本 -> tools/_reverted/%s' % os.path.basename(dst))
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        shutil.copy2(bp, lp)
        print('       已用副本覆盖活文件')

    if not bad:
        print('\n全部一致，无需处理。')
        return 0
    if not a.apply:
        print('\n发现 %d 处不一致。加 --apply 覆盖。' % bad)
        return 1
    print('\n已同步 %d 处。' % bad)
    return 0


if __name__ == '__main__':
    sys.exit(main())
