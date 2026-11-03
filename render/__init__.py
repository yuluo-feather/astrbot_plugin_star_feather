"""渲染包对外入口：只导出编排层直接使用的名字。

子模块：layout（几何）、assets（清单）、draw（绘制）、store（文件生命周期）、
fonts（字体）。内部调用与测试直接 import 子模块，不要从这里转发。
"""
from .draw import render_cards, render_daily_card
from .store import new_instance_image_dir

__all__ = ["render_cards", "render_daily_card", "new_instance_image_dir"]
