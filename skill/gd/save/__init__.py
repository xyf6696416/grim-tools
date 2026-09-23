"""存档层：解析 / 校验 / 备份 / 回写 恐怖黎明的 .gdc（角色）与 .gst（共享仓库）。

模块（由旧技能机械迁移而来，只补了「模块路径 / 环境探测 / 写死路径」三类接缝）：

    core.py    解密读取器 Reader / 编码器 encode / 15 个块的解析
    write.py   变长写入（换装、增删条目）
    patch.py   定长补丁（只改数值大小不变）
    inv.py     背包（袋子）物品增删复制
    backup.py  备份 / 校验 / 恢复
    verify.py  统一体检（全角色 / 单角色 / 与备份 diff / 写盘前安全检查）
    diff.py    逐字段 diff（写入前备份 vs 游戏重写后存档）

典型用法：

    from gd.save import core, verify, backup
    doc = core.parse(path)              # -> dict（15 个块）
    print(doc['b2'])                    # 属性块
"""

from . import backup, core, diff, inv, patch, verify, write          # noqa: F401

# 常用快捷方式
parse = core.parse
Reader = core.Reader
encode = core.encode

__all__ = ["core", "write", "patch", "inv", "backup", "verify", "diff",
           "parse", "Reader", "encode"]
