# `utils` 模块职责

Jenkins 只调用以下阶段入口；一个入口只完成一个流水线阶段的业务动作：

| Jenkins 阶段 | Python 入口 | 职责 |
| --- | --- | --- |
| Announce review start | `python -m utils.review_start` | 查重、开始通知和 reviewing 标签 |
| Fetch MR diff from GitLab API | `python -m utils.prepare_api_diff` | 获取并校验 API diff；以退出码 `10` 请求本地兜底 |
| Generate local Git diff fallback | `python -m utils.use_local_git_diff <path>` | 导入 Jenkins 生成的本地 diff |
| Review with DeepSeek | `python -m utils.ai_review` | 调用 AI，校验并写入结构化评审结果 |
| Publish review results | `python -m utils.publish_review` | 发布行级评论、降级汇总、完成标签和去重标记 |
| Jenkins `post { unsuccessful }` | `python -m utils.report_review_failure` | 发布失败标签和脱敏失败通知 |

复用代码不作为 Jenkins 入口：`common.py` 是环境、文件和 GitLab API 基础设施，`diff_utils.py` 处理 diff，`review_result.py` 处理 AI 结果格式与 Markdown。

`review.py` 是旧命令行参数的兼容转发器，仅供人工排障或尚未迁移的外部调用使用；它不包含评审业务实现，也不由 Jenkins 调用。
