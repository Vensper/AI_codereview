pipeline {
    agent { label 'ai-codereview-wenpeng0' }

    parameters {
        string(
            name: 'MANUAL_REPOSITORY_URL',
            defaultValue: 'https://gitlab.i-soft.com.cn/sao/ph-sao-qc-jjfa-2026.git',
            description: '仅手动构建备用；Webhook 构建优先使用 GitLab 事件中的仓库地址。'
        )
        string(
            name: 'MANUAL_SOURCE_BRANCH',
            defaultValue: '',
            description: '仅手动构建备用；填写 MR 来源分支。'
        )
        string(
            name: 'MANUAL_TARGET_BRANCH',
            defaultValue: 'master',
            description: '仅手动构建备用；填写 MR 目标分支。'
        )
        string(
            name: 'MANUAL_MR_IID',
            defaultValue: '',
            description: '仅手动构建备用；填写 GitLab MR IID，例如 6。'
        )
        string(
            name: 'MANUAL_PROJECT_ID',
            defaultValue: '',
            description: '仅手动构建备用；填写 GitLab 项目 ID。'
        )
        string(name: 'WEBHOOK_EVENT_KIND', defaultValue: '', description: 'Webhook 接收入口填写；手动构建留空。')
        string(name: 'WEBHOOK_MR_IID', defaultValue: '', description: '原始 GitLab 事件中的 MR IID。')
        string(name: 'WEBHOOK_TARGET_PROJECT_ID', defaultValue: '', description: '原始事件中的目标项目 ID。')
        string(name: 'WEBHOOK_SOURCE_PROJECT_ID', defaultValue: '', description: '原始事件中的来源项目 ID。')
        string(name: 'WEBHOOK_SOURCE_BRANCH', defaultValue: '', description: '原始事件中的来源分支。')
        string(name: 'WEBHOOK_TARGET_BRANCH', defaultValue: '', description: '原始事件中的目标分支。')
        string(name: 'WEBHOOK_SOURCE_REPO_HTTP_URL', defaultValue: '', description: '原始事件中的来源仓库 HTTP URL。')
        string(name: 'WEBHOOK_TARGET_REPO_HTTP_URL', defaultValue: '', description: '原始事件中的目标仓库 HTTP URL。')
        string(name: 'WEBHOOK_SOURCE_SHA', defaultValue: '', description: '原始事件中的来源提交 SHA。')
        booleanParam(
            name: 'PUBLISH_COMMENT',
            defaultValue: true,
            description: '是否向对应 GitLab MR 发布开始通知、审查评论和状态标签。'
        )
        booleanParam(
            name: 'FORCE_REVIEW',
            defaultValue: false,
            description: '仅手动排障使用：即使当前 MR 提交已成功审查，仍强制重新审查。'
        )
        booleanParam(
            name: 'FORCE_LOCAL_GIT_DIFF',
            defaultValue: false,
            description: '仅测试用：跳过 GitLab API diff，验证本地 Git diff 兜底路径。正式 Webhook 构建保持 false。'
        )
        choice(
            name: 'AI_EXECUTOR',
            choices: ['claude', 'codex', 'opencode'],
            description: 'AI CLI 执行器。默认 Claude Code；也可选择 Codex CLI 或 OpenCode CLI。'
        )
        choice(
            name: 'AI_PROVIDER',
            choices: ['deepseek', 'custom'],
            description: '模型服务。deepseek 根据执行器自动选择国内官方兼容接口；custom 用于其他兼容接口或国内统一网关。'
        )
        choice(
            name: 'AI_API_PROTOCOL',
            choices: ['auto', 'anthropic', 'openai_responses', 'openai_chat'],
            description: '接口协议。deepseek 使用 auto；custom 的 auto 会采用所选执行器的默认协议，也可显式指定。'
        )
        string(
            name: 'AI_BASE_URL',
            defaultValue: '',
            description: 'AI_PROVIDER=custom 时必填，填写与所选协议兼容的 HTTPS 根地址；deepseek 时忽略。'
        )
        string(
            name: 'AI_MODEL',
            defaultValue: '',
            description: '模型名称。deepseek 留空时使用 deepseek-flash[1m]；custom 必填。'
        )
        choice(
            name: 'AI_AUTH_STYLE',
            choices: ['bearer', 'api_key'],
            description: 'custom 接口鉴权方式：bearer 对应 Authorization: Bearer，api_key 对应 x-api-key；deepseek 时固定 bearer。'
        )
        string(
            name: 'AI_CREDENTIAL_ID',
            defaultValue: 'ai-model-custom-api-key',
            description: '仅 custom 使用：以 ai-model- 开头的 Jenkins Secret text 凭据 ID。只填写凭据 ID，不要填写 API Key。'
        )
    }

    options {
        skipDefaultCheckout(true)
        timestamps()
        disableConcurrentBuilds()
    }

    stages {
        stage('checkout') {
            steps {
                bat '''
                    @echo off
                    echo AI CLI diagnostics:
                    where codex
                    if errorlevel 1 (
                        echo codex executable: not found
                    ) else (
                        codex --version
                    )
                    where opencode
                    if errorlevel 1 (
                        echo opencode executable: not found
                    ) else (
                        opencode --version
                    )
                    exit /b 0
                '''
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    script {
                        env.REVIEW_SKIP = 'false'
                        env.REVIEW_FAILURE_STAGE = '识别 MR'
                        env.REVIEW_IDENTITY_CONFLICT = 'false'
                        def pluginAction = env.gitlabActionType?.trim()?.toUpperCase()
                        def noteCause = false
                        try {
                            noteCause = currentBuild.getBuildCauses(
                                'com.dabsquared.gitlabjenkins.cause.GitLabWebHookCause'
                            ).any { cause ->
                                cause?.data?.actionType?.toString()?.toUpperCase() == 'NOTE'
                            }
                        } catch (Exception ignored) {
                            echo '无法读取 GitLab 构建原因；继续使用插件变量和 Webhook 参数识别触发方式。'
                        }
                        def keywordPhrase = env.gitlabTriggerPhrase?.trim() == '/codereview'
                        def commentReview = pluginAction == 'NOTE' || noteCause || keywordPhrase ||
                            params.WEBHOOK_EVENT_KIND?.trim() == 'note'
                        def pluginEvent = ['MERGE', 'NOTE'].contains(pluginAction) || noteCause ||
                            env.gitlabMergeRequestIid?.trim() ||
                            (!pluginAction && env.gitlabSourceBranch?.trim() &&
                             env.gitlabTargetBranch?.trim() &&
                             env.gitlabSourceBranch.trim() != env.gitlabTargetBranch.trim())
                        def rawEvent = params.WEBHOOK_EVENT_KIND?.trim() in ['merge_request', 'note']
                        env.REVIEW_GITLAB_TRIGGER = (pluginEvent || rawEvent) ? 'true' : 'false'
                        env.REVIEW_BYPASS_DEDUP = (params.FORCE_REVIEW || commentReview) ? 'true' : 'false'

                        if (env.gitlabMergeRequestIid?.trim() && params.WEBHOOK_MR_IID?.trim() &&
                            env.gitlabMergeRequestIid.trim() != params.WEBHOOK_MR_IID.trim()) {
                            env.REVIEW_IDENTITY_CONFLICT = 'true'
                            error('插件变量与原始 Webhook 的 MR IID 冲突，停止回写以避免写错 MR。')
                        }
                        if (env.gitlabMergeRequestTargetProjectId?.trim() &&
                            params.WEBHOOK_TARGET_PROJECT_ID?.trim() &&
                            env.gitlabMergeRequestTargetProjectId.trim() != params.WEBHOOK_TARGET_PROJECT_ID.trim()) {
                            env.REVIEW_IDENTITY_CONFLICT = 'true'
                            error('插件变量与原始 Webhook 的目标项目 ID 冲突，停止回写以避免写错 MR。')
                        }

                        env.EFFECTIVE_REPOSITORY_URL =
                            env.gitlabSourceRepoHttpUrl?.trim() ?:
                            params.WEBHOOK_SOURCE_REPO_HTTP_URL?.trim() ?:
                            (env.REVIEW_GITLAB_TRIGGER == 'true' ? '' : params.MANUAL_REPOSITORY_URL.trim())

                        env.EFFECTIVE_TARGET_REPOSITORY_URL =
                            env.gitlabTargetRepoHttpUrl?.trim() ?:
                            params.WEBHOOK_TARGET_REPO_HTTP_URL?.trim() ?: ''

                        env.EFFECTIVE_SOURCE_BRANCH =
                            env.gitlabSourceBranch?.trim() ?:
                            params.WEBHOOK_SOURCE_BRANCH?.trim() ?:
                            (env.REVIEW_GITLAB_TRIGGER == 'true' ? '' : params.MANUAL_SOURCE_BRANCH.trim())

                        env.EFFECTIVE_TARGET_BRANCH =
                            env.gitlabTargetBranch?.trim() ?:
                            params.WEBHOOK_TARGET_BRANCH?.trim() ?:
                            (env.REVIEW_GITLAB_TRIGGER == 'true' ? '' : params.MANUAL_TARGET_BRANCH.trim())

                        env.EFFECTIVE_MR_IID =
                            env.gitlabMergeRequestIid?.trim() ?:
                            params.WEBHOOK_MR_IID?.trim() ?:
                            (env.REVIEW_GITLAB_TRIGGER == 'true' ? '' : params.MANUAL_MR_IID.trim())

                        env.EFFECTIVE_PROJECT_ID =
                            env.gitlabMergeRequestTargetProjectId?.trim() ?:
                            params.WEBHOOK_TARGET_PROJECT_ID?.trim() ?:
                            (env.REVIEW_GITLAB_TRIGGER == 'true' ? '' : params.MANUAL_PROJECT_ID.trim())

                        env.EFFECTIVE_SOURCE_PROJECT_ID = params.WEBHOOK_SOURCE_PROJECT_ID?.trim() ?: ''
                        env.EVENT_SOURCE_SHA =
                            env.gitlabMergeRequestLastCommit?.trim() ?:
                            params.WEBHOOK_SOURCE_SHA?.trim() ?: ''
                        env.REVIEW_PUBLISH_COMMENT =
                            (params.PUBLISH_COMMENT &&
                             (env.REVIEW_GITLAB_TRIGGER == 'true' ||
                              (env.EFFECTIVE_PROJECT_ID && env.EFFECTIVE_MR_IID))) ? 'true' : 'false'
                        if (env.REVIEW_GITLAB_TRIGGER != 'true' && env.REVIEW_PUBLISH_COMMENT != 'true') {
                            env.FORCE_LOCAL_GIT_DIFF = 'true'
                        }

                        if (!env.EFFECTIVE_REPOSITORY_URL) {
                            error('未获取仓库地址。请通过 GitLab MR Webhook 触发，或填写 MANUAL_REPOSITORY_URL。')
                        }
                        if (!env.EFFECTIVE_SOURCE_BRANCH) {
                            error('未获取来源分支。请通过 GitLab MR Webhook 触发，或填写 MANUAL_SOURCE_BRANCH。')
                        }
                        if (!env.EFFECTIVE_TARGET_BRANCH) {
                            error('未获取目标分支。请通过 GitLab MR Webhook 触发，或填写 MANUAL_TARGET_BRANCH。')
                        }
                        echo """
                            === Merge Request Context ===
                            Trigger: ${env.REVIEW_GITLAB_TRIGGER == 'true' ? 'GitLab event' : 'Manual build'}
                            Repository: ${env.EFFECTIVE_REPOSITORY_URL}
                            Project ID: ${env.EFFECTIVE_PROJECT_ID ?: 'unknown'}
                            MR IID: ${env.EFFECTIVE_MR_IID ?: 'unknown'}
                            Source branch: ${env.EFFECTIVE_SOURCE_BRANCH}
                            Target branch: ${env.EFFECTIVE_TARGET_BRANCH}
                            Publish comment: ${env.REVIEW_PUBLISH_COMMENT}
                            GitLab action type: ${pluginAction ?: 'unknown'}
                            Keyword comment detected: ${commentReview}
                            Bypass completed-review check: ${env.REVIEW_BYPASS_DEDUP}
                        """.stripIndent()
                    }
                    script { env.REVIEW_FAILURE_STAGE = '检出审查工具' }
                    deleteDir()
                    dir('review-tool') {
                        checkout([
                            $class: 'GitSCM',
                            branches: [[name: 'refs/heads/codex/multi-excutor']],
                            userRemoteConfigs: [[
                                url: 'https://gitlab.i-soft.com.cn/sao/ph-sao-qc-jjfa-2026.git',
                                credentialsId: 'gitlab-project-read'
                            ]]
                        ])
                        script {
                            if (!fileExists('utils/ai_review.py')) {
                                error('审查工具分支中找不到 utils/ai_review.py。')
                            }
                            if (!fileExists('utils/resolve_mr_context.py')) {
                                error('审查工具分支中找不到 utils/resolve_mr_context.py。')
                            }
                        }
                    }
                    script {
                        if (env.REVIEW_GITLAB_TRIGGER == 'true' &&
                            (!env.EFFECTIVE_PROJECT_ID?.trim() || !env.EFFECTIVE_MR_IID?.trim())) {
                            if (env.EFFECTIVE_MR_IID?.trim() ||
                                (env.EVENT_SOURCE_SHA ==~ /[0-9a-fA-F]{40}/)) {
                                env.REVIEW_FAILURE_STAGE = '识别 MR'
                                env.EFFECTIVE_SOURCE_SHA = env.EVENT_SOURCE_SHA
                                dir('review-tool') {
                                    withCredentials([
                                        string(credentialsId: 'gitlab-review-api', variable: 'GITLAB_API_TOKEN')
                                    ]) {
                                        bat '''
                                            @echo off
                                            python -u -m utils.resolve_mr_context "../mr-context-resolved.txt"
                                        '''
                                    }
                                }
                                def resolved = readFile('mr-context-resolved.txt').readLines()
                                if (resolved.size() != 2 ||
                                    !(resolved[0] ==~ /[1-9][0-9]*/) ||
                                    !(resolved[1] ==~ /[1-9][0-9]*/)) {
                                    error('MR 定位脚本未返回合法的目标项目 ID 和 MR IID。')
                                }
                                env.EFFECTIVE_PROJECT_ID = resolved[0]
                                env.EFFECTIVE_MR_IID = resolved[1]
                                echo "MR identity recovered: project=${env.EFFECTIVE_PROJECT_ID}, iid=${env.EFFECTIVE_MR_IID}"
                            } else {
                                echo '事件未提供有效源 SHA；待检出源分支后再唯一定位 MR。'
                            }
                        }
                    }
                    script { env.REVIEW_FAILURE_STAGE = '检出 MR 源分支' }
                    dir('mr-source') {
                        checkout([
                            $class: 'GitSCM',
                            branches: [[name: "refs/heads/${env.EFFECTIVE_SOURCE_BRANCH}"]],
                            userRemoteConfigs: [[
                                url: env.EFFECTIVE_REPOSITORY_URL,
                                credentialsId: 'gitlab-project-read',
                                refspec: '+refs/heads/*:refs/remotes/origin/*'
                            ]]
                        ])
                    }
                    script { env.REVIEW_FAILURE_STAGE = '获取源提交 SHA' }
                    dir('mr-source') {
                        script {
                            env.EFFECTIVE_SOURCE_SHA = bat(
                                script: '@echo off\r\ngit rev-parse HEAD',
                                returnStdout: true
                            ).trim()
                            if (!(env.EFFECTIVE_SOURCE_SHA ==~ /[0-9a-fA-F]{40}/)) {
                                error('无法取得 MR 源分支的 Git commit SHA。')
                            }
                            if (env.EVENT_SOURCE_SHA?.trim() &&
                                env.EVENT_SOURCE_SHA.toLowerCase() != env.EFFECTIVE_SOURCE_SHA.toLowerCase()) {
                                error('MR 来源分支已变化，检出提交与触发事件的 SHA 不一致。')
                            }
                            echo "Source SHA: ${env.EFFECTIVE_SOURCE_SHA}"
                        }
                    }
                    script {
                        if (env.REVIEW_GITLAB_TRIGGER == 'true' &&
                            (!env.EFFECTIVE_PROJECT_ID?.trim() || !env.EFFECTIVE_MR_IID?.trim())) {
                            env.REVIEW_FAILURE_STAGE = '识别 MR'
                            dir('review-tool') {
                                withCredentials([
                                    string(credentialsId: 'gitlab-review-api', variable: 'GITLAB_API_TOKEN')
                                ]) {
                                    bat '''
                                        @echo off
                                        python -u -m utils.resolve_mr_context "../mr-context-resolved.txt"
                                    '''
                                }
                            }
                            def resolved = readFile('mr-context-resolved.txt').readLines()
                            if (resolved.size() != 2 ||
                                !(resolved[0] ==~ /[1-9][0-9]*/) ||
                                !(resolved[1] ==~ /[1-9][0-9]*/)) {
                                error('MR 定位脚本未返回合法的目标项目 ID 和 MR IID。')
                            }
                            env.EFFECTIVE_PROJECT_ID = resolved[0]
                            env.EFFECTIVE_MR_IID = resolved[1]
                            echo "MR identity recovered: project=${env.EFFECTIVE_PROJECT_ID}, iid=${env.EFFECTIVE_MR_IID}"
                        }
                        if (env.REVIEW_PUBLISH_COMMENT == 'true' &&
                            (!env.EFFECTIVE_PROJECT_ID?.trim() || !env.EFFECTIVE_MR_IID?.trim())) {
                            error('无法唯一定位 GitLab MR；禁止向不确定的 MR 写入评论。')
                        }
                    }
                    script {
                        if (env.REVIEW_PUBLISH_COMMENT == 'true') {
                            env.REVIEW_FAILURE_STAGE = '发送开始通知'
                            dir('review-tool') {
                                withCredentials([
                                    string(
                                        credentialsId: 'gitlab-review-api',
                                        variable: 'GITLAB_API_TOKEN'
                                    )
                                ]) {
                                    bat '''
                                        @echo off
                                        python -u -m utils.review_start
                                    '''
                                }
                                script {
                                    if (fileExists('review-skip.flag')) {
                                        env.REVIEW_SKIP = 'true'
                                        currentBuild.description = "Skipped: MR commit ${env.EFFECTIVE_SOURCE_SHA.take(12)} already reviewed"
                                        echo 'Review deduplication: already completed, skip.'
                                    } else {
                                        echo 'Review deduplication: no completed review found.'
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        stage('ai review') {
            when { expression { env.REVIEW_SKIP != 'true' && currentBuild.currentResult == 'SUCCESS' } }
            steps {
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    script { env.REVIEW_FAILURE_STAGE = '准备预合并工作区' }
                    dir('review-tool') {
                        bat '''
                            @echo off
                            python -u -m utils.prepare_premerge "../mr-source" "../mr-premerge"
                        '''
                    }
                    script {
                        env.REVIEW_FAILURE_STAGE = '获取 GitLab MR diff'
                        env.DIFF_FALLBACK_REQUIRED = 'false'
                        env.DIFF_SOURCE = ''

                        int apiStatus
                        dir('review-tool') {
                            withCredentials([
                                string(
                                    credentialsId: 'gitlab-review-api',
                                    variable: 'GITLAB_API_TOKEN'
                                )
                            ]) {
                                apiStatus = bat(
                                    returnStatus: true,
                                    script: '''
                                        @echo off
                                        python -u -m utils.prepare_api_diff
                                    '''
                                )
                            }
                        }

                        if (apiStatus == 0) {
                            env.DIFF_SOURCE = 'gitlab_api'
                            echo 'MR diff source: GitLab API.'
                        } else if (apiStatus == 10) {
                            env.DIFF_SOURCE = 'local_git_fallback'
                            env.DIFF_FALLBACK_REQUIRED = 'true'
                            echo 'GitLab API diff is unavailable or incomplete; local Git fallback will be used.'
                        } else {
                            error("GitLab API diff preparation failed with exit code ${apiStatus}.")
                        }
                    }
                    script {
                        if (env.DIFF_FALLBACK_REQUIRED == 'true') {
                            env.REVIEW_FAILURE_STAGE = '生成本地 Git 兜底 diff'
                            dir('mr-source') {
                                bat '''
                                    @echo off
                                    setlocal
                                    set "TARGET=%EFFECTIVE_TARGET_BRANCH%"

                                    git rev-parse --verify "origin/%TARGET%" >nul 2>&1
                                    if errorlevel 1 (
                                        echo ERROR: Target branch origin/%TARGET% was not fetched.
                                        exit /b 1
                                    )

                                    echo === Local Git fallback: changed files ===
                                    git -c core.quotePath=false diff --name-status "origin/%TARGET%...HEAD" > changed-files.txt
                                    if errorlevel 1 exit /b 1

                                    echo === Local Git fallback: diff summary ===
                                    git -c core.quotePath=false diff --stat "origin/%TARGET%...HEAD"

                                    git -c core.quotePath=false diff --no-ext-diff --unified=3 "origin/%TARGET%...HEAD" > mr.diff.local
                                    if errorlevel 1 exit /b 1
                                '''
                            }
                            dir('review-tool') {
                                bat '''
                                    @echo off
                                    python -u -m utils.use_local_git_diff "../mr-source/mr.diff.local"
                                '''
                            }
                        }
                    }
                    script {
                        env.REVIEW_FAILURE_STAGE = '配置 AI 执行器'

                        def executor = params.AI_EXECUTOR?.trim()?.toLowerCase()
                        def provider = params.AI_PROVIDER?.trim()?.toLowerCase()
                        def baseUrl = ''
                        def model = params.AI_MODEL?.trim()
                        def protocol = params.AI_API_PROTOCOL?.trim()?.toLowerCase()
                        def authStyle = params.AI_AUTH_STYLE?.trim()?.toLowerCase()
                        def credentialId = ''
                        def opencodeCacheHome = env.OPENCODE_CACHE_HOME?.trim() ?: "${env.WORKSPACE}\\.opencode-provider-cache"

                        if (!(executor in ['claude', 'codex', 'opencode'])) {
                            error("不支持的 AI_EXECUTOR：${executor ?: '(empty)'}。")
                        }

                        if (provider == 'deepseek') {
                            model = model ?: 'deepseek-flash[1m]'
                            authStyle = 'bearer'
                            credentialId = 'deepseek-api-key'
                            if (executor == 'claude') {
                                baseUrl = 'https://api.deepseek.com/anthropic'
                                protocol = 'anthropic'
                            } else if (executor == 'codex') {
                                baseUrl = 'https://api.deepseek.com'
                                protocol = 'openai_responses'
                            } else {
                                baseUrl = 'https://api.deepseek.com'
                                protocol = 'openai_chat'
                            }
                        } else if (provider == 'custom') {
                            baseUrl = params.AI_BASE_URL?.trim()
                            credentialId = params.AI_CREDENTIAL_ID?.trim()
                            if (!baseUrl) {
                                error('AI_PROVIDER=custom 时必须填写 AI_BASE_URL。')
                            }
                            if (!model) {
                                error('AI_PROVIDER=custom 时必须填写 AI_MODEL。')
                            }
                            if (!(authStyle in ['bearer', 'api_key'])) {
                                error('AI_AUTH_STYLE 只能是 bearer 或 api_key。')
                            }
                            if (!(credentialId ==~ /^ai-model-[A-Za-z0-9._-]+$/)) {
                                error('custom 的 AI_CREDENTIAL_ID 必须以 ai-model- 开头，且只能包含字母、数字、点、下划线和连字符。')
                            }
                            if (protocol == 'auto') {
                                if (executor == 'claude') {
                                    protocol = 'anthropic'
                                } else if (executor == 'codex') {
                                    protocol = 'openai_responses'
                                } else {
                                    protocol = 'openai_chat'
                                }
                            }
                        } else {
                            error("不支持的 AI_PROVIDER：${provider ?: '(empty)'}。")
                        }

                        def supportedProtocols = [
                            claude: ['anthropic'],
                            codex: ['openai_responses'],
                            opencode: ['openai_chat', 'openai_responses']
                        ]
                        if (!(protocol in supportedProtocols[executor])) {
                            error("AI_EXECUTOR=${executor} 不支持 AI_API_PROTOCOL=${protocol}；允许值：${supportedProtocols[executor].join(', ')}。")
                        }
                        if (executor == 'opencode' && authStyle != 'bearer') {
                            error('OpenCode 的 OpenAI 兼容提供方当前只支持 AI_AUTH_STYLE=bearer。')
                        }

                        if (!(baseUrl ==~ /^https:\/\/[^\s?#]+\/?$/)) {
                            error('AI_BASE_URL 必须是无查询参数、无片段的 HTTPS 地址。')
                        }
                        echo "AI executor=${executor}; provider=${provider}; protocol=${protocol}; model=${model}; auth=${authStyle}; endpoint=${baseUrl}"
                        env.REVIEW_FAILURE_STAGE = 'AI 审查'
                        dir('review-tool') {
                            withCredentials([
                                string(
                                    credentialsId: credentialId,
                                    variable: 'AI_API_KEY'
                                )
                            ]) {
                                withEnv([
                                    "AI_EXECUTOR=${executor}",
                                    "AI_BASE_URL=${baseUrl}",
                                    "AI_MODEL_OVERRIDE=${model}",
                                    "AI_API_PROTOCOL=${protocol}",
                                    "AI_AUTH_STYLE=${authStyle}",
                                    "OPENCODE_CACHE_HOME=${opencodeCacheHome}"
                                ]) {
                                    bat '''
                                        @echo off
                                        python -u -m utils.ai_review "../mr-premerge"
                                    '''
                                }
                            }
                        }
                    }
                }
            }
        }
        stage('report') {
            steps {
                script {
                    def earlierFailure = currentBuild.currentResult != 'SUCCESS'
                    def reportError = null

                    if (!earlierFailure && env.REVIEW_SKIP != 'true') {
                        env.REVIEW_FAILURE_STAGE = '发布审查结果'
                        try {
                            dir('review-tool') {
                                withCredentials([
                                    string(
                                        credentialsId: 'gitlab-review-api',
                                        variable: 'GITLAB_API_TOKEN'
                                    )
                                ]) {
                                    bat '''
                                        @echo off
                                        python -u -m utils.publish_review
                                    '''
                                }
                            }
                        } catch (Exception failure) {
                            reportError = failure
                        }
                    }

                    try {
                        if (!earlierFailure && reportError == null) {
                            env.REVIEW_FAILURE_STAGE = '归档审查产物'
                        }
                        archiveArtifacts(
                            artifacts: 'mr-source/mr.diff.local, mr-source/changed-files.txt, review-tool/mr.diff, review-tool/diff-source.json, review-tool/diff-api-fallback-reason.txt, review-tool/premerge-result.json, review-tool/ai-tool-trace.json, review-tool/review-skip.flag, review-tool/review.schema.json, review-tool/review-raw-initial.txt, review-tool/review-raw-structured-initial.txt, review-tool/review-raw.txt, review-tool/review-validation.txt, review-tool/review-result.json, review-tool/review-result.md',
                            allowEmptyArchive: true
                        )
                    } catch (Exception failure) {
                        if (reportError == null) {
                            reportError = failure
                        } else {
                            echo '审查产物归档也失败了；保留原始发布错误。'
                        }
                    }

                    if (earlierFailure || reportError != null) {
                        if (env.REVIEW_GITLAB_TRIGGER != 'true') {
                            echo '手动构建失败；无需向 GitLab MR 回写失败通知。'
                        } else if (env.REVIEW_IDENTITY_CONFLICT == 'true') {
                            echo 'MR 标识来源互相冲突；禁止回写以避免写错 MR。'
                        } else if (env.REVIEW_PUBLISH_COMMENT != 'true') {
                            echo 'GitLab 评论发布已关闭；跳过失败通知。'
                        } else if (!env.EFFECTIVE_PROJECT_ID?.trim() || !env.EFFECTIVE_MR_IID?.trim()) {
                            echo '缺少 GitLab Project ID 或 MR IID，无法确定失败通知的目标 MR。'
                        } else {
                            try {
                                withCredentials([
                                    string(
                                        credentialsId: 'gitlab-review-api',
                                        variable: 'GITLAB_API_TOKEN'
                                    )
                                ]) {
                                    if (fileExists('review-tool/utils/report_review_failure.py')) {
                                        dir('review-tool') {
                                            bat '''
                                                @echo off
                                                python -u -m utils.report_review_failure
                                            '''
                                        }
                                    } else {
                                        // 启动兜底：review-tool 尚未成功检出时 Python reporter 不可用。
                                        // REVIEW_FAILURE_STAGE 只由本 Jenkinsfile 设置；这里只做长度和换行校验，
                                        // 避免再维护一份容易漂移的阶段白名单。
                                        def stageName = env.REVIEW_FAILURE_STAGE?.trim()
                                        if (!stageName || stageName.length() > 40 ||
                                            stageName.contains('\r') || stageName.contains('\n')) {
                                            stageName = '未知阶段'
                                        }
                                        def buildLink = env.BUILD_URL?.trim() ?: '请查看 Jenkins 构建记录'
                                        env.REVIEW_FAILURE_NOTE = "AI代码审核失败。\n\n" +
                                            "- 失败阶段：${stageName}\n" +
                                            "- Jenkins 构建：${buildLink}\n" +
                                            '- 详细错误请查看 Jenkins 控制台日志；此处不公开接口响应或凭证。'
                                        powershell(
                                            encoding: 'UTF-8',
                                            script: '''

                                                $ErrorActionPreference = 'Stop'
                                                $baseUrl = $env:GITLAB_BASE_URL
                                                if ([string]::IsNullOrWhiteSpace($baseUrl)) {
                                                    $baseUrl = 'https://gitlab.i-soft.com.cn'
                                                }
                                                $baseUri = $null
                                                if (-not [Uri]::TryCreate($baseUrl, [UriKind]::Absolute, [ref]$baseUri) -or
                                                    $baseUri.Scheme -notin @('http', 'https')) {
                                                    throw 'Invalid GitLab URL.'
                                                }
                                                $project = [Uri]::EscapeDataString($env:EFFECTIVE_PROJECT_ID.Trim())
                                                $iid = $env:EFFECTIVE_MR_IID.Trim()
                                                if ($iid -notmatch '^[0-9]+$') {
                                                    throw 'Invalid MR IID.'
                                                }
                                                $api = $baseUrl.TrimEnd('/') + '/api/v4/projects/' +
                                                    $project + '/merge_requests/' + $iid
                                                $headers = @{ 'PRIVATE-TOKEN' = $env:GITLAB_API_TOKEN }
                                                $contentType = 'application/x-www-form-urlencoded; charset=utf-8'
                                                $note = $env:REVIEW_FAILURE_NOTE
                                                if ([string]::IsNullOrWhiteSpace($note)) {
                                                    throw 'Failure note is empty.'
                                                }

                                                function Get-RetryDelay($errorRecord, $attempt) {
                                                    $response = $errorRecord.Exception.Response
                                                    $statusCode = 0
                                                    if ($null -ne $response) {
                                                        $statusCode = [int]$response.StatusCode
                                                    }
                                                    if ($statusCode -notin @(429, 502, 503, 504)) {
                                                        $webStatus = $errorRecord.Exception.Status
                                                        if ($statusCode -ne 0 -or $webStatus -notin @(
                                                            'Timeout', 'ConnectFailure', 'ConnectionClosed',
                                                            'ReceiveFailure', 'SendFailure', 'NameResolutionFailure',
                                                            'ProxyNameResolutionFailure'
                                                        )) {
                                                            return $null
                                                        }
                                                    }
                                                    $retryAfter = if ($null -ne $response) {
                                                        $response.Headers['Retry-After']
                                                    } else { $null }
                                                    if ($retryAfter) {
                                                        $seconds = 0.0
                                                        if ([double]::TryParse($retryAfter, [ref]$seconds)) {
                                                            if ($seconds -gt 10) { return $null }
                                                            return [Math]::Max(0, $seconds)
                                                        }
                                                        $retryAt = [DateTimeOffset]::MinValue
                                                        if (-not [DateTimeOffset]::TryParse($retryAfter, [ref]$retryAt)) {
                                                            return $null
                                                        }
                                                        $seconds = ($retryAt - [DateTimeOffset]::UtcNow).TotalSeconds
                                                        if ($seconds -gt 10) { return $null }
                                                        return [Math]::Max(0, $seconds)
                                                    }
                                                    return [Math]::Min([Math]::Pow(2, $attempt), 10)
                                                }

                                                function Invoke-GitLabRequest($method, $uri, $body, $attempts) {
                                                    for ($attempt = 0; $attempt -lt $attempts; $attempt++) {
                                                        try {
                                                            $requestParams = @{
                                                                Method = $method; Uri = $uri; Headers = $headers
                                                                TimeoutSec = 10; ErrorAction = 'Stop'
                                                            }
                                                            if ($null -ne $body) {
                                                                $requestParams.ContentType = $contentType
                                                                $requestParams.Body = $body
                                                            }
                                                            return Invoke-RestMethod @requestParams
                                                        } catch {
                                                            $delay = Get-RetryDelay $_ $attempt
                                                            if ($null -eq $delay -or $attempt + 1 -ge $attempts) {
                                                                throw
                                                            }
                                                            Start-Sleep -Seconds $delay
                                                        }
                                                    }
                                                }

                                                $notesUri = $api + '/notes'
                                                function Test-FailureNoteExists {
                                                    for ($page = 1; $page -le 20; $page++) {
                                                        $uri = $notesUri + '?per_page=100&order_by=created_at&sort=desc&page=' + $page
                                                        $result = Invoke-GitLabRequest 'Get' $uri $null 3
                                                        $notes = @($result)
                                                        foreach ($existing in $notes) {
                                                            if ($null -ne $existing -and $existing.author.id -eq $user.id -and
                                                                $existing.body -like ('*' + $marker + '*')) {
                                                                return $true
                                                            }
                                                        }
                                                        if ($notes.Count -lt 100) { return $false }
                                                    }
                                                    throw 'GitLab failure note lookup exceeded the safe pagination limit.'
                                                }

                                                $failed = @()
                                                try {
                                                    $labelBody = 'add_labels=ai_code_review_failed&' +
                                                        'remove_labels=ai_code_reviewing%2Cai_code_review_done'
                                                    Invoke-GitLabRequest 'Put' $api $labelBody 3 | Out-Null
                                                } catch {
                                                    $failed += 'label: ' + $_.Exception.Message
                                                }
                                                try {
                                                    $buildTag = $env:BUILD_TAG
                                                    if ($buildTag -notmatch '^[A-Za-z0-9_.-]+$') {
                                                        throw 'BUILD_TAG is required to publish a deduplicated failure note.'
                                                    }
                                                    $marker = '<!-- AI_CODE_REVIEW_FAILURE:' + $buildTag + ' -->'
                                                    $note = $note.TrimEnd() + "`n`n" + $marker + "`n"
                                                    $user = Invoke-GitLabRequest 'Get' ($baseUrl.TrimEnd('/') + '/api/v4/user') $null 3
                                                    if ($null -eq $user.id) {
                                                        throw 'GitLab API token cannot identify its current user.'
                                                    }
                                                    $noteBody = 'body=' + [Uri]::EscapeDataString($note)
                                                    for ($attempt = 0; $attempt -lt 3; $attempt++) {
                                                        if (Test-FailureNoteExists) { break }
                                                        try {
                                                            Invoke-GitLabRequest 'Post' $notesUri $noteBody 1 | Out-Null
                                                            break
                                                        } catch {
                                                            $delay = Get-RetryDelay $_ $attempt
                                                            if ($null -eq $delay) { throw }
                                                            if ($attempt -eq 2) {
                                                                if (Test-FailureNoteExists) { break }
                                                                throw
                                                            }
                                                            Start-Sleep -Seconds $delay
                                                        }
                                                    }
                                                } catch {
                                                    $failed += 'note: ' + $_.Exception.Message
                                                }
                                                if ($failed.Count -gt 0) {
                                                    throw ('GitLab failure report failed: ' + ($failed -join ', '))
                                                }
                                                Write-Output 'GitLab failure label and note published.'
                                            '''
                                        )
                                    }
                                }
                            } catch (ignored) {
                                echo '无法更新 GitLab 失败标签或通知；请查看本次 Jenkins 构建日志。'
                            }
                        }
                    }
                    if (reportError != null) {
                        throw reportError
                    }
                }
            }
        }
    }
}
