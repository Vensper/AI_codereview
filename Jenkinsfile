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
            description: '仅手动构建备用；第二阶段 GitLab API 会使用此项目 ID。'
        )
        booleanParam(
            name: 'PUBLISH_COMMENT',
            defaultValue: true,
            description: 'Webhook 构建时，是否向对应 GitLab MR 回写普通审查评论。'
        )
    }

    options {
        skipDefaultCheckout(true)
        timestamps()
    }

    stages {
        stage('Identify Merge Request') {
            steps {
                script {
                    // Webhook 信息优先；仅当手动构建时才使用 MANUAL_* 参数。
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

                    echo """
                        === Merge Request Context ===
                        Trigger: ${env.gitlabMergeRequestIid ? 'GitLab Webhook' : 'Manual build'}
                        Repository: ${env.EFFECTIVE_REPOSITORY_URL}
                        Project ID: ${env.EFFECTIVE_PROJECT_ID ?: 'unknown'}
                        MR IID: ${env.EFFECTIVE_MR_IID ?: 'unknown'}
                        Source branch: ${env.EFFECTIVE_SOURCE_BRANCH}
                        Target branch: ${env.EFFECTIVE_TARGET_BRANCH}
                    """.stripIndent()
                }
            }
        }

        stage('Checkout source branch') {
            steps {
                checkout([
                    $class: 'GitSCM',
                    branches: [[name: "*/${env.EFFECTIVE_SOURCE_BRANCH}"]],
                    userRemoteConfigs: [[
                        url: env.EFFECTIVE_REPOSITORY_URL,
                        credentialsId: 'gitlab-project-read'
                    ]]
                ])
            }
        }

        stage('Generate MR diff') {
            steps {
                bat '''
                    @echo off
                    set "TARGET=%EFFECTIVE_TARGET_BRANCH%"

                    echo === Changed files ===
                    git diff --name-status "origin/%TARGET%...HEAD"

                    echo === Diff summary ===
                    git diff --stat "origin/%TARGET%...HEAD"

                    git diff --no-ext-diff --unified=3 "origin/%TARGET%...HEAD" > mr.diff

                    echo === Diff created ===
                    dir mr.diff
                '''
            }
        }

        stage('Review with DeepSeek') {
            steps {
                withCredentials([
                    string(
                        credentialsId: 'deepseek-api-key',
                        variable: 'DEEPSEEK_API_KEY'
                    )
                ]) {
                    retry(2) {
                        bat '''
                            @echo off
                            python scripts\\review.py
                        '''
                    }
                }
            }
        }

        stage('Publish review to GitLab MR') {
            when {
                expression {
                    return params.PUBLISH_COMMENT &&
                        env.gitlabMergeRequestIid?.trim()
                }
            }

            steps {
                script {
                    def review = readFile(file: 'review-result.md')

                    def comment = """\
## 🤖 AI 代码审查结果

- Jenkins 构建：#${env.BUILD_NUMBER}
- MR：!${env.EFFECTIVE_MR_IID}
- 源分支：`${env.EFFECTIVE_SOURCE_BRANCH}`
- 目标分支：`${env.EFFECTIVE_TARGET_BRANCH}`

---

${review}
""".stripIndent()

                    addGitLabMRComment(comment: comment)
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'mr.diff, review-result.md', allowEmptyArchive: true
        }
    }
}
