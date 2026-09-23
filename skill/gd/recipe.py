# -*- coding: utf-8 -*-
r"""gd/recipe.py —— **薄壳**。真正的实现在 `tools/recipe.py`（BD 配方 v2 编排器）。

为什么改薄壳
------------
v1（2026-09-17）把六步流水线写死在这里，用 `cd data && python gd_opt.py` 调装备搜索、
还要 `bd_gen.py` 出报告。这两个脚本在现有工具链里**都不存在**了
（装备搜索已并入 `python -m gd auto`，报告走 `gd/planreport.py`）⇒ 实跑第③步必崩：

    ✗ 搜索失败：python.exe: can't open file '...\data\gd_opt.py': No such file

v2 的定位是**只做编排**：每阶段都是对现有工具的一次子进程调用，
所以底层工具演进时 recipe 自动跟着变，不需要在两处维护同一份实现。
分阶段状态落 `data/plans/recipe/<key>/state.json`，支持续跑。

用法（与 v1 同名，参数全新）：见 `tools/recipe.py` 的模块文档，或::

    python -m gd recipe --help
"""
import pathlib
import runpy

_SKILL = pathlib.Path(__file__).resolve().parent.parent
_ns = runpy.run_path(str(_SKILL / 'tools' / 'recipe.py'), run_name='gd_recipe_v2')
main = _ns['main']

if __name__ == '__main__':
    raise SystemExit(main())
