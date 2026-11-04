# 安全策略

## 支持范围

只对最新发布版本提供安全修复（当前 0.9.x）。旧版本请先升级到最新版再报告。

## 报告漏洞

请优先使用 GitHub 的私密漏洞报告：

https://github.com/yuluo-feather/astrbot_plugin_star_feather/security/advisories/new

不要在公开 issue 里贴漏洞细节、密钥、token 或真实聊天记录。

## 请提供

- 插件版本（`metadata.yaml` 的 `version`，或 WebUI 插件页显示的版本）
- AstrBot 版本与平台适配器（aiocqhttp / WebChat / 其他）
- 复现步骤，以及预期行为和实际行为
- 相关日志（请先脱敏）

## 处理方式

- 会在方便时尽快确认收到，并给出初步判断；这是个人维护的项目，无法承诺固定时限
- 修复发布后会在 changelog 中说明；如你愿意，可以署名致谢
- 没有漏洞赏金

## 不在范围内

- AstrBot 本体、模型服务或平台适配器自身的漏洞，请报告给对应上游
- 因本机配置不当（例如把管理凭据贴进群聊）导致的问题
