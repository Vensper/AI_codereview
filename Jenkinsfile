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
    }

    options {
        skipDefaultCheckout(true)
        timestamps()
        disableConcurrentBuilds()
    }

    stages {
        stage('Identify Merge Request') {
            steps {
                script {
                    env.REVIEW_SKIP = 'false'
                    env.EFFECTIVE_REPOSITORY_URL =
                        env.gitlabSourceRepoHttpUrl?.trim() ?:
                        params.MANUAL_REPOSITORY_URL.trim()

                    env.EFFECTIVE_SOURCE_BRANCH =
                        env.gitlabSourceBranch?.trim() ?:
                        params.MANUAL_SOURCE_BRANCH.trim()

                    env.EFFECTIVE_TARGET_BRANCH =
                        env.gitlabTargetBranch?.trim() ?:
                        params.MANUAL_TARGET_BRANCH.trim()

                    env.EFFECTIVE_MR_IID =
                        env.gitlabMergeRequestIid?.trim() ?:
                        params.MANUAL_MR_IID.trim()

                    env.EFFECTIVE_PROJECT_ID =
                        env.gitlabMergeRequestTargetProjectId?.trim() ?:
                        params.MANUAL_PROJECT_ID.trim()

                    if (!env.EFFECTIVE_REPOSITORY_URL) {
                        error('未获取仓库地址。请通过 GitLab MR Webhook 触发，或填写 MANUAL_REPOSITORY_URL。')
                    }
                    if (!env.EFFECTIVE_SOURCE_BRANCH) {
                        error('未获取来源分支。请通过 GitLab MR Webhook 触发，或填写 MANUAL_SOURCE_BRANCH。')
                    }
                    if (!env.EFFECTIVE_TARGET_BRANCH) {
                        error('未获取目标分支。请通过 GitLab MR Webhook 触发，或填写 MANUAL_TARGET_BRANCH。')
                    }
                    if (params.PUBLISH_COMMENT &&
                        (!env.EFFECTIVE_PROJECT_ID || !env.EFFECTIVE_MR_IID)) {
                        error('启用评论发布时，必须取得 GitLab Project ID 和 MR IID。')
                    }

                    echo """
                        === Merge Request Context ===
                        Trigger: ${env.gitlabMergeRequestIid ? 'GitLab Webhook' : 'Manual build'}
                        Repository: ${env.EFFECTIVE_REPOSITORY_URL}
                        Project ID: ${env.EFFECTIVE_PROJECT_ID ?: 'unknown'}
                        MR IID: ${env.EFFECTIVE_MR_IID ?: 'unknown'}
                        Source branch: ${env.EFFECTIVE_SOURCE_BRANCH}
                        Target branch: ${env.EFFECTIVE_TARGET_BRANCH}
                        Publish comment: ${params.PUBLISH_COMMENT}
                    """.stripIndent()
                }
            }
        }

        stage('Checkout trusted reviewer') {
            steps {
                deleteDir()
                dir('review-tool') {
                    checkout([
                        $class: 'GitSCM',
                        branches: [[name: 'refs/heads/master']],
                        userRemoteConfigs: [[
                            url: 'https://gitlab.i-soft.com.cn/sao/ph-sao-qc-jjfa-2026.git',
                            credentialsId: 'gitlab-project-read'
                        ]]
                    ])
                    script {
                        if (!fileExists('utils/review.py')) {
                            error('受保护的 master 中找不到 utils/review.py。')
                        }
                    }
                }
            }
        }

        stage('Checkout source branch') {
            steps {
                dir('mr-source') {
                    checkout([
                        $class: 'GitSCM',
                        branches: [[name: "refs/heads/${env.EFFECTIVE_SOURCE_BRANCH}"]],
                        userRemoteConfigs: [[
                            url: env.EFFECTIVE_REPOSITORY_URL,
                            credentialsId: 'gitlab-project-read'
                        ]]
                    ])
                }
            }
        }

        stage('Capture source revision') {
            steps {
                dir('mr-source') {
                    script {
                        env.EFFECTIVE_SOURCE_SHA = bat(
                            script: '@echo off\r\ngit rev-parse HEAD',
                            returnStdout: true
                        ).trim()
                        if (!(env.EFFECTIVE_SOURCE_SHA ==~ /[0-9a-fA-F]{40}/)) {
                            error('无法取得 MR 源分支的 Git commit SHA。')
                        }
                        echo "Source SHA: ${env.EFFECTIVE_SOURCE_SHA}"
                    }
                }
            }
        }

        stage('Announce review start') {
            when { expression { params.PUBLISH_COMMENT } }
            steps {
                script {
                    env.REVIEW_FAILURE_STAGE = '发送开始通知'
                    env.REVIEW_NOTICE_ATTEMPTED = 'true'
                }
                dir('review-tool') {
                    withCredentials([
                        string(
                            credentialsId: 'gitlab-review-api',
                            variable: 'GITLAB_API_TOKEN'
                        )
                    ]) {
                        bat '''
                            @echo off
                            python -u utils/review.py --start
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

        stage('Generate MR diff') {
            when { expression { env.REVIEW_SKIP != 'true' } }
            steps {
                script { env.REVIEW_FAILURE_STAGE = '生成 MR diff' }
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

                    echo === Changed files ===
                    git -c core.quotePath=false diff --name-status "origin/%TARGET%...HEAD"
                    git -c core.quotePath=false diff --name-status "origin/%TARGET%...HEAD" > changed-files.txt

                    echo === Diff summary ===
                    git -c core.quotePath=false diff --stat "origin/%TARGET%...HEAD"

                    git -c core.quotePath=false diff --no-ext-diff --unified=3 "origin/%TARGET%...HEAD" > mr.diff
                    if errorlevel 1 exit /b 1

                        echo === Diff created ===
                        dir mr.diff changed-files.txt
                    '''
                }
                bat '''
                    @echo off
                    copy /Y "mr-source\\mr.diff" "review-tool\\mr.diff" >nul
                    if errorlevel 1 exit /b 1
                '''
            }
        }

        stage('Review and publish') {
            when { expression { env.REVIEW_SKIP != 'true' } }
            steps {
                script { env.REVIEW_FAILURE_STAGE = 'AI 审查或结果发布' }
                dir('review-tool') {
                    withCredentials([
                        string(
                            credentialsId: 'deepseek-api-key',
                            variable: 'DEEPSEEK_API_KEY'
                        ),
                        string(
                            credentialsId: 'gitlab-review-api',
                            variable: 'GITLAB_API_TOKEN'
                        )
                    ]) {
                        bat '''
                            @echo off
                            python -u utils/review.py --review
                        '''
                    }
                }
            }
        }
    }

    post {
        unsuccessful {
            script {
                if (env.REVIEW_NOTICE_ATTEMPTED == 'true') {
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
                                    python -u utils/review.py --fail
                                '''
                            }
                        }
                    } catch (ignored) {
                        echo '无法更新 GitLab 失败标签或通知；请查看本次 Jenkins 构建日志。'
                    }
                }
            }
        }
        cleanup {
            archiveArtifacts(
                artifacts: 'mr-source/mr.diff, mr-source/changed-files.txt, review-tool/review-skip.flag, review-tool/review-raw.txt, review-tool/review-result.json, review-tool/review-result.md',
                allowEmptyArchive: true
            )
        }
    }
}
