# `utils` 模块职责

Jenkins 只调用以下阶段入口；一个入口只完成一个流水线阶段的业务动作：

| Jenkins 阶段 | Python 入口 | 职责 |
| --- | --- | --- |
| Announce review start | `python -m utils.review_start` | 查重、开始通知和 reviewing 标签；通知或标签失败时继续审查 |
| Resolve MR identity | `python -m utils.resolve_mr_context <output-file>` | 插件与 Webhook 参数缺失时，按项目、分支和 SHA 唯一定位 MR |
| Fetch MR diff from GitLab API | `python -m utils.prepare_api_diff` | 获取并校验 API diff；以退出码 `10` 请求本地兜底 |
| Generate local Git diff fallback | `python -m utils.use_local_git_diff <path>` | 导入 Jenkins 生成的本地 diff |
| Review with configured AI executor | `python -m utils.ai_review` | 共用提示词、`review.schema.json`、本地校验和一次重试；Python 层默认 Claude Code，Jenkins 参数接入前现有流水线显式保留 legacy DeepSeek |
| Publish review results | `python -m utils.publish_review` | 逐条发布行级评论；单次 POST 汇总并同时写入 SUMMARY/DONE 标记，随后更新完成标签 |
| `report` 阶段失败回写 | `python -m utils.report_review_failure`；脚本不可用时由 Jenkinsfile 的 PowerShell 兜底 | 发布失败标签和脱敏失败通知，保留合并前的具体步骤名 |

复用代码不作为 Jenkins 入口：`common.py` 是环境、文件和 GitLab API 基础设施，`diff_utils.py` 处理 diff，`review_result.py` 处理 AI 结果格式与 Markdown。

`webhook_receiver.py` 是可选的 GitLab Webhook 接收服务，负责从原始事件提取 MR 信息并传给 Jenkins；部署方法见 `docs/webhook-receiver.md`。

`review.py` 是旧命令行参数的兼容转发器，仅供人工排障或尚未迁移的外部调用使用；它不包含评审业务实现，也不由 Jenkins 调用。

评论发布以提交级隐藏 `AI_CODE_REVIEW_DONE` 标记为完成事实，以 MR 标签为可修复的展示状态。
普通构建开始时如果找到当前源提交的 DONE 标记，会先把标签修复为 `ai_code_review_done`
（并移除 reviewing/failed），然后跳过 AI 和重复评论；DONE 查询或标签修复失败会直接使构建失败。
发布结果无法通过标记确认时不会继续写入后续正常评论，Jenkins 仍会尽量归档已有产物，
并沿用现有 `report_review_failure` 逻辑和格式回写失败状态。
普通自动构建的行级评论标记按源提交和评论内容保持跨构建稳定，失败后的下一次构建
可以复用已成功发布的 Discussion；强制审查使用构建级标记，允许明确创建新一轮评论。

## AI 执行器（第一阶段）

`AI_EXECUTOR` 未设置时默认 `claude`；当前还接受 `deepseek` 以兼容既有直接 API 流程。
Claude Code 使用 `AI_API_KEY`、`ANTHROPIC_AUTH_TOKEN` 或 `ANTHROPIC_API_KEY` 认证，
可用 `AI_BASE_URL` 指向 Anthropic Messages 兼容网关、用 `AI_MODEL_OVERRIDE` 指定模型。
`AI_API_KEY` 默认映射到 `x-api-key`；Bearer 网关需同时设置 `AI_AUTH_STYLE=bearer`。
执行器只启用 Read、Glob、Grep，禁用 `.git` 读取、插件、MCP、命令执行、写入和会话持久化。

每次 AI 审查共用一个 600 秒单调时钟截止时间：调查阶段最多 480 秒，进入最终生成后
最多再使用 120 秒，且不能越过总截止时间。64 个可观测模型轮次和 128 次可观测工具调用
仅作为异常保险丝，不是正常截断条件。CLI 超时或触发保险丝时会终止完整进程树；只有最终
生成进程正常退出、输出通过本地 `review.schema.json` 校验后才会写入 `review-result.json`
供发布阶段使用。结构化输出的一次重试也计入同一个截止时间和保险丝。

Jenkinsfile 当前显式设置 `AI_EXECUTOR=deepseek`，用于在构建参数和 Claude 凭据接入前保持现网行为；
后续参数化阶段会移除该过渡设置并让构建页面默认选择 Claude Code。
