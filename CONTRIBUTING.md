# 贡献指南

感谢愿意帮忙。这个项目以「小步、可验证」为原则，提交前请先看下面几条。

## 报告问题

- 抽牌、解读、图片、配置确实出错 → 用「报告一个问题」模板
- 想要新功能或新玩法 → 用「提一个想法」模板
- 用法、文档、市场页疑问 → 用「其他问题」模板
- 安全问题不要开公开 issue，见 [SECURITY.md](SECURITY.md)

## 开发环境

- Python 3.12+，AstrBot >= 4.16,<5
- 安装开发工具链：`pip install -r requirements-dev.txt`
- 单元测试不要求本机装 AstrBot（`tests/conftest.py` 提供桩；契约测试在没装框架时会跳过）
- 常用命令：

  ```bash
  python -m pytest
  python -m ruff check .
  python -m mypy
  ```

测试文件的组织与导入约定见 README 的「技术实现」末段。

## 提交 PR

- 从 `main` 开分支，PR 也指向 `main`
- 提交信息使用 conventional commits：`fix: ...`、`feat: ...`、`docs: ...`
- 只改与问题相关的文件；不要顺手重排、格式化无关文件
- 新行为请补测试，测试放在 `tests/` 下按域分文件
- 不要在 PR 里改版本号或 `changelogs/`，发版由维护者处理
- 不要提交 `.env`、token、真实聊天记录或本机绝对路径

## 代码风格

- 沿用现有模块边界与命名，不引入新依赖，除非确有必要
- 注释与用户可见文案沿用当前中文风格；用户可见文案集中在 `core/texts.py`
- 改动范围较大或涉及架构时，先开 issue 讨论

## 许可

贡献代码按 [AGPL-3.0](LICENSE) 授权。
