# PH-SAO-QC-JJFA-2026

//test 2026/9/21 17：15
//test 2026/9/21 17：15
//test 2026/9/22 17：16
//test 2026/9/23 16：05
//test 2026/9/23 16：15
//test 2026/9/23 16：27
//test 2026/9/23 16：30
//test 2026/9/23 16：33
//test 2026/9/24 16：03
//test 2026/9/24 17:59
//test 2026/9/24 18:00
//test 2026/9/24 18:02
//test 2026/9/24 18:07
//test 2026/10/8 10:15
## Jenkins 构建进度

此流水线使用三个阶段作为 Jenkins 任务主页的进度列：`checkout`（识别 MR、检出、查重）、`ai review`（预合并、准备 diff、AI 审查）、`report`（发布 GitLab 评审结果、失败通知及产物归档）。前两个阶段失败后仍会进入 `report`，并保留失败时的具体步骤名。每次构建占一行，构建编号、当前阶段的进度条和阶段用时由 Jenkins 的 **Pipeline: Stage View** 插件显示；运行中刷新任务主页即可查看更新。

GitLab 触发时，MR 标识依次取自 Jenkins GitLab 插件变量、[原始 Webhook 接收入口](docs/webhook-receiver.md)传入的参数、GitLab API 的唯一匹配查询。无法唯一定位时停止回写，避免评论写错 MR。手动构建不回写失败通知；未提供 MR 标识的手动构建使用本地 Git diff。

在 MR 评论 `/codereview` 触发的构建会重新审查，即使该提交已经审查完成；普通 MR 事件继续按提交 SHA 查重。GitLab 插件入口需在 Jenkins 任务中启用 Comments 触发并设置 `/codereview` 匹配规则，同时在 GitLab Webhook 中启用评论事件。手动构建仍可用 `FORCE_REVIEW` 参数强制重新审查。构建日志中的 `Keyword comment detected` 和 `Bypass completed-review check` 显示本次是否识别为评论触发并跳过查重。

GitLab API 短时超时、连接中断、429 或 5xx 时，读取和标签更新最多尝试 3 次，每次请求超时 10 秒；429 遵守不超过 10 秒的 `Retry-After`，超过当场重试预算就停止。失败评论使用 Jenkins `BUILD_TAG` 作为隐藏标识；POST 失败后先查询该 MR 是否已有本次构建的机器人评论，再决定是否重试。查询也失败时停止发布，避免盲目重复评论。401/403 和证书错误不重试；持续不可达时错误保留在 Jenkins 日志中，不进行恢复后的补发。

汇总评论和行级 Discussion 也在首次 POST 中写入由 `BUILD_TAG`、源提交 SHA 和问题指纹组成的隐藏标识。POST 超时后先查询同一机器人发布的标识：已存在即视为成功，确认不存在才有限重试，查询失败则停止发布。行级评论只有在确认未发布后才能降级到汇总区；降级内容随汇总评论的标识一起受到保护。每次 `/codereview` 会产生新的 `BUILD_TAG`，因此仍可对相同提交强制重审。

开始通知使用同样的隐藏标识确认和安全重试。开始通知或 `ai_code_reviewing` 标签暂时无法写入时，流水线记录警告并继续 AI 审查；普通 MR 事件的完成查重查询仍必须成功，避免在无法确认历史状态时重复审查。

AI 审查由 Jenkins Agent 上 npm 默认安装的 Claude Code、Codex CLI 或 OpenCode CLI 执行，构建参数 `AI_EXECUTOR` 默认选择 `claude`。`AI_PROVIDER=deepseek` 时，流水线按执行器自动选择 DeepSeek 官方 Anthropic、OpenAI Responses 或 OpenAI Chat 兼容接口，并使用现有 `deepseek-api-key`；`custom` 可连接其他兼容接口或部署在国内网络中的统一模型网关，并引用以 `ai-model-` 开头的专用 Jenkins Secret text 凭据。API Key 不进入普通参数、命令行或日志。调查阶段仅允许只读检索预合并工作区，最终阶段禁用工具；三个执行器共用 `review.schema.json`、本地校验和一次重试，第二次仍失败则停止发布评审内容并进入 GitLab 失败回写。

若任务主页没有 Stage View，请在 Jenkins 的插件管理中安装 `pipeline-stage-view`，然后打开对应 Pipeline 任务的主页或 **Full Stage View**。插件默认显示最近 10 次构建；要显示更多历史构建，需由 Jenkins 管理员调整 `com.cloudbees.workflow.rest.external.JobExt.maxRunsPerJob`。跳过重复审查时，`ai review` 显示为跳过，`report` 仍执行产物归档；前两个阶段失败时，`report` 仍执行失败通知和产物归档。



new test 2026/9/9 11:25
new test 2026/9/9 17:38
new test 2026/9/9 17:46
new test 202/9/10 11:13
new test 202/9/12 16:10
new test 202/9/15 14:28
new test 202/9/15 14:53
new test 202/9/21 18:00
new test 202/9/21 18:08
## Getting started

To make it easy for you to get started with GitLab, here's a list of recommended next steps.

Already a pro? Just edit this README.md and make it your own. Want to make it easy? [Use the template at the bottom](#editing-this-readme)!

## Add your files

- [ ] [Create](https://docs.gitlab.com/ee/user/project/repository/web_editor.html#create-a-file) or [upload](https://docs.gitlab.com/ee/user/project/repository/web_editor.html#upload-a-file) files
- [ ] [Add files using the command line](https://docs.gitlab.com/ee/gitlab-basics/add-file.html#add-a-file-using-the-command-line) or push an existing Git repository with the following command:

```
cd existing_repo
git remote add origin https://gitlab.i-soft.com.cn/sao/ph-sao-qc-jjfa-2026.git
git branch -M master
git push -uf origin master
```

## Integrate with your tools

- [ ] [Set up project integrations](https://gitlab.i-soft.com.cn/sao/ph-sao-qc-jjfa-2026/-/settings/integrations)

## Collaborate with your team

- [ ] [Invite team members and collaborators](https://docs.gitlab.com/ee/user/project/members/)
- [ ] [Create a new merge request](https://docs.gitlab.com/ee/user/project/merge_requests/creating_merge_requests.html)
- [ ] [Automatically close issues from merge requests](https://docs.gitlab.com/ee/user/project/issues/managing_issues.html#closing-issues-automatically)
- [ ] [Enable merge request approvals](https://docs.gitlab.com/ee/user/project/merge_requests/approvals/)
- [ ] [Automatically merge when pipeline succeeds](https://docs.gitlab.com/ee/user/project/merge_requests/merge_when_pipeline_succeeds.html)

## Test and Deploy

Use the built-in continuous integration in GitLab.

- [ ] [Get started with GitLab CI/CD](https://docs.gitlab.com/ee/ci/quick_start/index.html)
- [ ] [Analyze your code for known vulnerabilities with Static Application Security Testing(SAST)](https://docs.gitlab.com/ee/user/application_security/sast/)
- [ ] [Deploy to Kubernetes, Amazon EC2, or Amazon ECS using Auto Deploy](https://docs.gitlab.com/ee/topics/autodevops/requirements.html)
- [ ] [Use pull-based deployments for improved Kubernetes management](https://docs.gitlab.com/ee/user/clusters/agent/)
- [ ] [Set up protected environments](https://docs.gitlab.com/ee/ci/environments/protected_environments.html)

***

# Editing this README

When you're ready to make this README your own, just edit this file and use the handy template below (or feel free to structure it however you want - this is just a starting point!). Thank you to [makeareadme.com](https://www.makeareadme.com/) for this template.

## Suggestions for a good README
Every project is different, so consider which of these sections apply to yours. The sections used in the template are suggestions for most open source projects. Also keep in mind that while a README can be too long and detailed, too long is better than too short. If you think your README is too long, consider utilizing another form of documentation rather than cutting out information.

## Name
Choose a self-explaining name for your project.

## Description
Let people know what your project can do specifically. Provide context and add a link to any reference visitors might be unfamiliar with. A list of Features or a Background subsection can also be added here. If there are alternatives to your project, this is a good place to list differentiating factors.

## Badges
On some READMEs, you may see small images that convey metadata, such as whether or not all the tests are passing for the project. You can use Shields to add some to your README. Many services also have instructions for adding a badge.

## Visuals
Depending on what you are making, it can be a good idea to include screenshots or even a video (you'll frequently see GIFs rather than actual videos). Tools like ttygif can help, but check out Asciinema for a more sophisticated method.

## Installation
Within a particular ecosystem, there may be a common way of installing things, such as using Yarn, NuGet, or Homebrew. However, consider the possibility that whoever is reading your README is a novice and would like more guidance. Listing specific steps helps remove ambiguity and gets people to using your project as quickly as possible. If it only runs in a specific context like a particular programming language version or operating system or has dependencies that have to be installed manually, also add a Requirements subsection.

## Usage
Use examples liberally, and show the expected output if you can. It's helpful to have inline the smallest example of usage that you can demonstrate, while providing links to more sophisticated examples if they are too long to reasonably include in the README.

## Support
Tell people where they can go to for help. It can be any combination of an issue tracker, a chat room, an email address, etc.

## Roadmap
If you have ideas for releases in the future, it is a good idea to list them in the README.

## Contributing
State if you are open to contributions and what your requirements are for accepting them.

For people who want to make changes to your project, it's helpful to have some documentation on how to get started. Perhaps there is a script that they should run or some environment variables that they need to set. Make these steps explicit. These instructions could also be useful to your future self.

You can also document commands to lint the code or run tests. These steps help to ensure high code quality and reduce the likelihood that the changes inadvertently break something. Having instructions for running tests is especially helpful if it requires external setup, such as starting a Selenium server for testing in a browser.

## Authors and acknowledgment
Show your appreciation to those who have contributed to the project.

## License
For open source projects, say how it is licensed.

## Project status
If you have run out of energy or time for your project, put a note at the top of the README saying that development has slowed down or stopped completely. Someone may choose to fork your project or volunteer to step in as a maintainer or owner, allowing your project to keep going. You can also make an explicit request for maintainers.
