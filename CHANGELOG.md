# 更新日志

所有显著的变更都会记录在此文档中。此项目遵循 [语义化版本控制](https://semver.org/spec/v2.0.0.html)。

## [2.0.2] - 2025-04-11

### 优化
- 进一步优化查找种子下载链接的匹配逻辑，增加显式等待和重试机制，减少 `stale element reference` 异常的发生，提高下载成功的概率。

### 修复
- 修复一些已知问题。

### 升级
- 升级Google Chrome浏览器和ChromeDriver版本到135.0.7049.84。

## [2.0.1] - 2025-04-10

### 优化
- 优化查找种子下载链接的匹配逻辑。
- 优化一些其他细节。

## [2.0.0] - 2025-04-08

### 说明
- 本次版本为v2.0.0，基于mediamaster-chromedriver 1.8.1版本改版并重新构建大部分代码，优化了部分功能。

### 注意事项
- 本次更新涉及数据库结构变更、配置文件变更，无法与之前版本兼容以及无损更新，需要重新进行系统配置。

[2.0.0]: https://github.com/smysong/mediamaster-v2/releases/tag/v2.0.0
[2.0.1]: https://github.com/smysong/mediamaster-v2/releases/tag/v2.0.1
[2.0.2]: https://github.com/smysong/mediamaster-v2/releases/tag/v2.0.2