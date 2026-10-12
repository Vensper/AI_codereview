//pipeline {}：定义整个流水线。
pipeline {
    //agent {}：指定流水线在哪里运行。
    agent { label 'ai-codereview-wenpeng0' }

    parameters {
        string(
            name: 'ai_excutor',
            defaultValue: 'cc',
            description: 'AI 执行器：cc=Claude Code，cx=Codex，oc=OpenCode。'
        )
        string(
            name: 'PROJECT_ID',
            defaultValue: '',
            description: 'GitLab 目标项目 ID；Webhook 触发时自动从事件中读取。'
        )
        string(
            name: 'dev_repository',
            defaultValue: 'https://gitlab.i-soft.com.cn/sao/ph-sao-qc-jjfa-2026.git',
            description: 'MR 来源仓库地址；Webhook 触发时自动从事件中读取。'
        )
        string(
            name: 'SOURCE_BRANCH',
            defaultValue: '',
            description: 'MR 来源分支；Webhook 触发时自动从事件中读取。'
        )
        string(
            name: 'MR_IID',
            defaultValue: '',
            description: 'GitLab MR IID，例如 34；Webhook 触发时自动从事件中读取。'
        )
        string(
            name: 'TARGET_BRANCH',
            defaultValue: 'master',
            description: 'MR 目标分支；Webhook 触发时自动从事件中读取。'
        )
    }

    options {
        // 默认情况下，Jenkins 在某些流水线配置中会自动检出当前 Job 关联的 SCM 代码。
        // 工作区由后续步骤分别检出审查工具和 MR 源码（自己决定检出什么代码），
        // 避免 Jenkins 自动检出错误分支。
        skipDefaultCheckout(true)
        //为 Jenkins 控制台日志增加时间信息
        timestamps()
        // 因为同一 Job 共用固定工作区，所以禁止构建并发，可避免不同构建互相覆盖检出内容和审查产物。
        disableConcurrentBuilds()
    }
    //stages {}：定义流水线具体执行哪些任务。
    stages {
        stage('checkout') {
            steps {
                //启动 Windows CMD 执行脚本
                bat '''
                    REM 关闭后续命令回显，并且不显示关闭回显的这条命令本身
                    @echo off

                    echo AI CLI diagnostics:

                    REM 寻找 Codex 可执行命令
                    where codex

                    REM 找到命令，不代表 Codex 一定能正常运行
                    REM 判断上一条命令是否执行失败,在 Windows CMD 中，命令执行后通常会产生一个退出状态码
                    REM 0成功，1失败或有问题，2+其他错误（取决于命令）
                    REM 如果上一条命令的退出码大于或等于 1，就执行括号中的代码。
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
                    
                    REM /b：退出当前批处理脚本，而不是直接关闭整个 CMD 会话；退出码 0表示成功
                    exit /b 0

                    REM 只是检查环境，不是正式调用 AI 审核。即使 Codex 没有安装，也不希望这个诊断步骤立即中断整个流水线。
                    REM 但是后面的正式 AI 执行步骤必须再次校验执行器是否可用。
                '''
                // checkout 阶段即使失败也继续进入 report，由 report 统一归档现场并尝试发送失败通知。
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    script {
                        //当前审查暂时不跳过
                        env.REVIEW_SKIP = 'false'
                        //如果接下来失败，记录失败发生在“识别 MR”环节
                        env.REVIEW_FAILURE_STAGE = '识别 MR'
                        //暂时没有发现 MR 身份冲突
                        env.REVIEW_IDENTITY_CONFLICT = 'false'
                        /*
                            ?. 是安全调用；变量不存在时返回 null，不会抛空指针异常。
                            trim() 去掉首尾空格。
                            toUpperCase() 统一转成大写
                            gitlabActionType通常由 GitLab Jenkins 插件在 Webhook 触发构建时动态注入，
                            是本次 GitLab Webhook 的事件动作类型，比如MERGE NOTE
                        */
                        def pluginAction = env.gitlabActionType?.trim()?.toUpperCase()
                        // 默认不是评论触发
                        def noteCause = false
                        try {
                            //这些 GitLab 构建原因中，有没有一个的 actionType 是 NOTE？有就是true
                            noteCause = currentBuild.getBuildCauses(
                                'com.dabsquared.gitlabjenkins.cause.GitLabWebHookCause'
                            ).any { cause ->
                                cause?.data?.actionType?.toString()?.toUpperCase() == 'NOTE'
                            }
                        } catch (Exception ignored) {
                            echo '无法读取 GitLab 构建原因；继续使用 GitLab 插件环境变量识别触发方式。'
                        }
                        def keywordPhrase = env.gitlabTriggerPhrase?.trim() == '/codereview'
                        // NOTE 事件或 /codereview 评论属于主动重审，不受“该提交已审查”去重规则限制。
                        def commentReview = pluginAction == 'NOTE' || noteCause || keywordPhrase
                        // 部分 GitLab 插件版本不会稳定提供 actionType，因此同时用 MR 字段组合识别 Webhook。
                        /*
                            证据1：actionType 是 MERGE/NOTE
                            证据2：Build Cause 是 NOTE
                            证据3：存在 MR IID
                            证据4：存在不同的源/目标分支
                        */
                        //这次 Jenkins 构建是否被识别为 GitLab 插件相关事件？
                        def pluginEvent = ['MERGE', 'NOTE'].contains(pluginAction) || noteCause ||
                            env.gitlabMergeRequestIid?.trim() ||
                            (!pluginAction && env.gitlabSourceBranch?.trim() &&
                             env.gitlabTargetBranch?.trim() &&
                             env.gitlabSourceBranch.trim() != env.gitlabTargetBranch.trim())
                        env.REVIEW_GITLAB_TRIGGER = pluginEvent ? 'true' : 'false'
                        env.REVIEW_BYPASS_DEDUP = (commentReview || !pluginEvent) ? 'true' : 'false'

                        // Webhook 数据优先，手动构建参数兜底；后续步骤只读取 EFFECTIVE_*，避免两套来源混用。
                        //
                        env.EFFECTIVE_REPOSITORY_URL =
                            env.gitlabSourceRepoHttpUrl?.trim() ?:
                            params.dev_repository?.trim()

                        env.EFFECTIVE_TARGET_REPOSITORY_URL =
                            env.gitlabTargetRepoHttpUrl?.trim() ?: ''

                        env.EFFECTIVE_SOURCE_BRANCH =
                            env.gitlabSourceBranch?.trim() ?:
                            params.SOURCE_BRANCH?.trim()

                        env.EFFECTIVE_TARGET_BRANCH =
                            env.gitlabTargetBranch?.trim() ?:
                            params.TARGET_BRANCH?.trim()

                        env.EFFECTIVE_MR_IID =
                            env.gitlabMergeRequestIid?.trim() ?:
                            params.MR_IID?.trim()

                        env.EFFECTIVE_PROJECT_ID =
                            env.gitlabMergeRequestTargetProjectId?.trim() ?:
                            params.PROJECT_ID?.trim()

                        env.EFFECTIVE_SOURCE_PROJECT_ID =
                            env.gitlabMergeRequestSourceProjectId?.trim() ?: ''
                        //MR原分支最新提交的sha，sha是MR中每个提交的唯一哈希标识
                        env.EVENT_SOURCE_SHA = env.gitlabMergeRequestLastCommit?.trim() ?: ''
                        env.REVIEW_PUBLISH_COMMENT =
                            (env.REVIEW_GITLAB_TRIGGER == 'true' ||
                             (env.EFFECTIVE_PROJECT_ID && env.EFFECTIVE_MR_IID)) ? 'true' : 'false'
                        // 没有可回写的 MR 时按本地分支比较生成 diff，保证手动调试仍可完成审查。
                        if (env.REVIEW_GITLAB_TRIGGER != 'true' && env.REVIEW_PUBLISH_COMMENT != 'true') {
                            env.FORCE_LOCAL_GIT_DIFF = 'true'
                        }

                        // 仓库和分支是检出与生成 diff 的最低必要信息，缺失时立即给出可操作的错误。
                        if (!env.EFFECTIVE_REPOSITORY_URL) {
                            error('未获取仓库地址。请通过 GitLab MR Webhook 触发，或填写 dev_repository。')
                        }
                        if (!env.EFFECTIVE_SOURCE_BRANCH) {
                            error('未获取来源分支。请通过 GitLab MR Webhook 触发，或填写 SOURCE_BRANCH。')
                        }
                        if (!env.EFFECTIVE_TARGET_BRANCH) {
                            error('未获取目标分支。请通过 GitLab MR Webhook 触发，或填写 TARGET_BRANCH。')
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
                    // 固定节点可能残留上一次构建内容，先清空当前工作目录再分别建立 review-tool 和 mr-source 目录。
                    deleteDir()
                    dir('review-tool') {//创建并切换到文件夹
                        // 审查逻辑固定取工具分支，避免被审 MR 修改审查脚本本身。
                        checkout([//检出 Git 代码，下载到当前目录
                            $class: 'GitSCM',//使用 Git SCM 来获取源码
                            branches: [[name: 'refs/heads/codex/codex-cli']],//固定分支里面取工具
                            userRemoteConfigs: [[
                                url: 'https://gitlab.i-soft.com.cn/sao/ph-sao-qc-jjfa-2026.git',
                                credentialsId: 'gitlab-project-read'//gitlab access token
                            ]]
                        ])
                        script {
                            if (!fileExists('utils/ai_review.py')) {
                                //主动抛出错误，让当前 Pipeline 失败
                                error('审查工具分支中找不到 utils/ai_review.py。')
                            }
                            if (!fileExists('utils/resolve_mr_context.py')) {
                                error('审查工具分支中找不到 utils/resolve_mr_context.py。')
                            }
                        }
                    }
                    script {
                        // 某些 Webhook 缺少目标项目 ID；若已有 IID 或源 SHA，先调用 API 恢复唯一 MR 身份。
                        // 项目id和mrid是否完整？如果不完整，有没有查MR的线索？（mrid和sha）
                        //如果有线索，运行resolve_mr_context.py，调用gitlabApi查MR得到项目id和mrid
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
                        // 拉取所有远端分支，使后续本地兜底可以直接与 origin/<目标分支> 比较。
                        //这里不是必须要所有分支，而是 Codex 为了让后面的兜底逻辑省事，选择把所有远端分支都准备好。
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
                            // Webhook 到实际检出之间分支可能被再次推送；SHA 不一致时停止，避免评论到过期版本。
                            if (env.EVENT_SOURCE_SHA?.trim() &&
                                env.EVENT_SOURCE_SHA.toLowerCase() != env.EFFECTIVE_SOURCE_SHA.toLowerCase()) {
                                error('MR 来源分支已变化，检出提交与触发事件的 SHA 不一致。')
                            }
                            echo "Source SHA: ${env.EFFECTIVE_SOURCE_SHA}"
                        }
                    }
                    script {
                        // 首轮无法定位时，使用刚检出的准确 HEAD SHA 再尝试一次 MR 反查。
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
                            // 宁可终止也不向身份不确定的 MR 写入标签或评论。
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
                                    // review_start 同时负责完成态去重，并用标志文件把结果传回 Pipeline。
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
            // checkout 失败或相同提交已完成审查时，不再消耗模型调用资源。
            when { expression { env.REVIEW_SKIP != 'true' && currentBuild.currentResult == 'SUCCESS' } }
            steps {
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    script { env.REVIEW_FAILURE_STAGE = '准备预合并工作区' }
                    dir('review-tool') {
                        // 在独立目录构造“目标分支 + MR 变更”的预合并结果，AI 审查的是合并后的真实代码状态。
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

                        // prepare_api_diff 用约定退出码区分“可安全兜底”和“脚本异常”，防止静默掩盖真实错误。
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

                                    REM 三点比较以共同祖先为起点，只保留来源分支相对目标分支引入的改动。
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

                        // 参数使用短代码保持 Jenkins 表单简洁，这里统一映射为审查脚本识别的执行器名称。
                        def executorCode = params.ai_excutor?.trim()?.toLowerCase() ?: 'cc'
                        def executorNames = [cc: 'claude', cx: 'codex', oc: 'opencode']
                        def executor = executorNames[executorCode]
                        def opencodeCacheHome = env.OPENCODE_CACHE_HOME?.trim() ?: "${env.WORKSPACE}\\.opencode-provider-cache"

                        if (!executor) {
                            error("不支持的 ai_excutor：${executorCode}；只允许 cc、cx、oc。")
                        }

                        dir('review-tool') {
                            // 路由解析脚本按执行器选择协议、模型、鉴权方式和 Jenkins 凭证 ID。
                            withEnv(["AI_EXECUTOR=${executor}"]) {
                                bat '''
                                    @echo off
                                    python -u -m utils.resolve_ai_gateway "../ai-gateway-route.txt"
                                '''
                            }
                        }
                        def gatewayRoute = readFile('ai-gateway-route.txt').readLines()
                        // 路由文件是跨进程接口，严格校验字段数量和空值后才注入 AI CLI 环境。
                        if (gatewayRoute.size() != 5 || gatewayRoute.any { !it?.trim() }) {
                            error('统一模型网关路由解析结果无效。')
                        }
                        def protocol = gatewayRoute[0].trim()
                        def baseUrl = gatewayRoute[1].trim()
                        def model = gatewayRoute[2].trim()
                        def authStyle = gatewayRoute[3].trim()
                        def credentialId = gatewayRoute[4].trim()
                        echo "AI executor=${executor}; provider=gateway; protocol=${protocol}; model=${model}; auth=${authStyle}; endpoint=${baseUrl}"
                        env.REVIEW_FAILURE_STAGE = 'AI 审查'
                        dir('review-tool') {
                            // 密钥仅在模型调用作用域内绑定，日志只输出路由元数据，不输出密钥内容。
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

                    // 仅在前置阶段成功且未命中去重时发布正式审查结果。
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
                            // 先记住发布异常，仍继续归档本地结果，便于事后排查和人工补发。
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
                        // 发布错误优先级更高；若发布已失败，不用归档错误覆盖最初根因。
                        if (reportError == null) {
                            reportError = failure
                        } else {
                            echo '审查产物归档也失败了；保留原始发布错误。'
                        }
                    }

                    if (earlierFailure || reportError != null) {
                        // 回写失败通知前逐项确认触发来源和 MR 身份，避免手动构建或冲突信息误写 GitLab。
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
                                                # 兜底脚本直接发起 HTTP 请求，先限制 URL 协议，避免无效或危险地址。
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
                                                        # 只重试限流、网关异常和短暂网络故障，业务类 4xx 直接返回原始错误。
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
                                                        # 尊重 GitLab 的 Retry-After，但拒绝在 Jenkins 执行器上等待超过 10 秒。
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
                                                    # 所有 API 调用统一设置超时并复用相同的有限重试策略。
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
                                                    # 仅匹配当前 Token 用户发布且包含本次构建标记的评论，避免误判他人内容。
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
                                                # 标签和评论分别执行并收集错误，尽量完成其中仍可成功的一项。
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
                                                    # 隐藏标记使失败评论具备幂等性；网络重试前后都会检查是否已经写入。
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
                        // 归档和失败通知尝试结束后，重新抛出发布异常，确保最终构建状态仍为失败。
                        throw reportError
                    }
                }
            }
        }
    }
}
