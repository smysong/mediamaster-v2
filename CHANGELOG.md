# 更新日志

所有显著的变更都会记录在此文档中。此项目遵循 [语义化版本控制](https://semver.org/spec/v2.0.0.html)。

## [2.1.3] - 2025-04-30

### 说明
- 本次更新为五一节前最后一次更新，计划节后将开始开发免费站点功能，计划适配：观影、不太灵影视 两个站点。

### 优化
- 正在订阅数据插入数据库时加入豆瓣ID到数据库中，便于后续资源搜索时使用豆瓣ID进行搜索（计划用于免费站点）。

### 修复
- 修复从服务控制中手动运行服务后运行日志被删除的问题。

## [2.1.2] - 2025-04-29

### 优化
- 在线升级时，加载指示器遮罩层被升级窗口遮挡的问题。
- 使用qbittorrent下载器时，任务暂停状态显示为未知的问题。
- 优化Github代理加速，智能选择响应时间最快的代理站点，提高在线升级时拉取新版本的成功率。

## [2.1.1] - 2025-04-27

### 优化
- 优化版本号读取逻辑，版本号不再直接写在程序代码中，通过读取版本文件获取版本号。
- 优化版本统计报告，程序启动后将随机生成一个UUID，并向服务器报告当前程序的UUID和当前版本号（仅用于统计使用情况，程序不会收集任何用户个人数据）。

### 修复
- 修复系统仪表CPU、内存、存储使用情况显示，超80%以上使用率时仪表颜色变为橙色，超90%以上使用率时仪表颜色变为红色，提升用户体验。

## [2.1.0] - 2025-04-24

### 优化
- 调整WEB UI界面样式，页面观感更加美观，提升用户体验。

### 修复
- 修复一些已知问题，提升用户体验。

### 新增
- 上线系统仪表盘统计功能，支持显示当前媒体库中的媒体统计，以及系统硬件资源使用情况、系统进程显示、下载器实时速率显示等。
- 上线TMDB热门推荐功能，支持从推荐中直接添加订阅。（此功能依赖TMDB API接口，网络不可达或API密钥无效时无法正常使用。）

## [2.0.5] - 2025-04-17

### 优化
- 优化部分页面显示效果，提升用户体验。
- 优化在线升级功能处理逻辑，提升用户体验。

## [2.0.4] - 2025-04-16

### 新增
- 新增在线升级功能，支持在线检查程序更新并且进行在线升级（依赖Github Api接口，网络不可达会导致检查更新失败）。

## [2.0.3] - 2025-04-15

### 优化
- 优化下载器管理功能，使用qbittorrent-api和transmission-rpc python库，并且统一前端页面api接口调用，支持qbittorrent和transmission下载器。

### 修复
- 修复一些已知问题，提升用户体验。

### 新增
- 新增下载器管理功能支持用户名密码认证，在系统设置中配置下载器用户名和密码即可（qbittorrent一般需要登录，transmission一般不需要；如果没有用户名密码时配置项保持默认或填写为空即可）。

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
[2.0.3]: https://github.com/smysong/mediamaster-v2/releases/tag/v2.0.3
[2.0.4]: https://github.com/smysong/mediamaster-v2/releases/tag/v2.0.4
[2.0.5]: https://github.com/smysong/mediamaster-v2/releases/tag/v2.0.5
[2.1.0]: https://github.com/smysong/mediamaster-v2/releases/tag/v2.1.0
[2.1.1]: https://github.com/smysong/mediamaster-v2/releases/tag/v2.1.1
[2.1.2]: https://github.com/smysong/mediamaster-v2/releases/tag/v2.1.2
[2.1.3]: https://github.com/smysong/mediamaster-v2/releases/tag/v2.1.3