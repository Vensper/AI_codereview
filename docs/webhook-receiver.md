# GitLab MR 标识兜底接入

GitLab 插件入口直接使用插件环境变量；可选原始 Webhook 入口把事件转换成与手动构建相同的 5 个 MR 参数。缺失标识时使用 GitLab API 唯一匹配查询。手动构建不向 GitLab 回写失败通知。零匹配或多匹配都会停止回写。

## 现有插件入口

原来的 GitLab 插件 `/project/<job>` Webhook 可以继续使用。插件未提供 MR IID 或目标项目 ID 时，流水线在检出 `review-tool` 后，按目标项目、来源项目、来源与目标分支以及触发提交 SHA 查询 GitLab。只有唯一匹配才采用查询结果。若事件没有 SHA，会在检出来源分支取得 SHA 后再查询。

## 原始 Webhook 入口

`python -m utils.webhook_receiver` 是无需额外 Jenkins 插件的可选入口。它验证 GitLab Webhook Secret Token，从 MR 事件或 MR 上的 `/codereview` 评论提取项目、MR 和分支信息，再通过 Jenkins `buildWithParameters` 启动同一个 Pipeline 任务。它只发送 `PROJECT_ID`、`dev_repository`、`SOURCE_BRANCH`、`MR_IID`、`TARGET_BRANCH`；`ai_excutor` 使用 Jenkins 默认值 `cc`。

MR 事件仅接受新建和含 `oldrev` 的代码更新；标签等普通更新会被忽略，以免流水线写入 GitLab 标签时反复触发自身。

接入步骤：

1. 将此分支部署到 GitLab 能访问的机器，在 HTTPS 反向代理后运行接收服务。配置环境变量 `GITLAB_WEBHOOK_SECRET`、`JENKINS_JOB_URL`（任务 URL，不是 `/project` Webhook URL）、`JENKINS_API_USER` 和 `JENKINS_API_TOKEN`。可配置 `WEBHOOK_LISTEN_HOST`（默认 `127.0.0.1`）、`WEBHOOK_LISTEN_PORT`（默认 `8097`）和 `GITLAB_ALLOWED_TARGET_PROJECT_ID`。接收服务不执行 GitLab API 查询，以便尽快响应 Webhook；缺失标识由 Jenkins 构建查询。
2. 将更新后的 Jenkinsfile 用作该 Pipeline 的 SCM 脚本，先运行一次以注册精简后的 6 个构建参数。Jenkins API 用户需有该任务的 Build 权限；使用 API Token 调用 `buildWithParameters`。
3. 将 GitLab 项目 Webhook 指向接收服务的 `/gitlab`，设置相同的 Secret Token，并启用 **Merge request events** 与 **Comment events**。对同一事件停用原来的 `/project/<job>` 投递，避免重复构建。
4. 在 MR 上分别触发一次更新和 `/codereview` 评论，检查构建日志中的 `Project ID`、`MR IID` 与 `Trigger`。评论命令要求整条评论（去掉首尾空格后）恰好是 `/codereview`。

接收服务只把必要的 MR 字段传入 Jenkins，不把整个 Webhook 请求体或密钥写入构建参数。通过该入口启动的构建在流水线内按手动构建处理并自动跳过完成查重；因为包含项目 ID 和 MR IID，仍会正常发布审查结果。Jenkins 中的 GitLab API 凭证仍使用 `gitlab-review-api`。如果项目 ID、分支无法唯一定位 MR，流水线会失败并在 Jenkins 日志中说明，绝不会猜测 MR 后写入评论。
