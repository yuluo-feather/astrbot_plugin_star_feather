"""框架适配层：core / services 需要的端口在这里落地成 AstrBot 调用。

不在包根再导出子模块：`services` 只导入 `adapters.clock` / `adapters.kv_utils`
这两个窄封装，包根一旦急切导入 `astrbot_io`，整条链就会把宿主重新拉进
服务层的导入路径（2026-11-10 由 `test_import_model` 的动态用例钉住）。
需要包根名字的调用方请直接导入子模块。
"""
