# 隐私说明 / Privacy notes

[简体中文 README](README.md) · [English README](README.en.md)

## 中文

JobPilot 是本地单用户应用。分发源码中的 `data/` 只保留 `.gitkeep`，示例简历和职位用于演示。不要将自己使用后的数据库、原始简历、导出文档或截图直接加入公开仓库。

### 本机保存什么

`data/jobpilot.db` 保存提取的简历文本、结构化字段、历史版本、职位、评分、生成文档、原文快照、设置及日志。它不保留上传的 PDF / DOCX 二进制源文件。

数据库**未加密**。配置 AI 或 SMTP 后，API Key / 邮箱凭据也保存在此数据库。接口对这两类字段打码，但这不等于对磁盘加密。具有本机文件权限的程序仍可能访问这些内容。停止服务后备份整个 `data/`，并保护备份。

### 哪些内容会外发

- LinkedIn 抓取：查询关键词和地区；可选代理会处理连接。
- AI：启用后将结构化简历、联系信息、岗位描述及基线草稿发给配置的模型服务商；API Key 用于认证。连接测试也会向该服务商发送测试请求。
- 邮件：向 SMTP 服务商认证并发送岗位汇总；EML / mailto 草稿在用户通过邮件客户端发送后离开本机。
- 网络诊断、代理探测及用户触发的可选依赖安装也可能建立网络连接。

因此，“本地存储”不代表使用所有功能时“数据完全不出本机”。是否启用外部功能由用户决定。

### 安全与事实检查边界

服务绑定回环地址，并检查 Host、Origin 与写请求类型，减少跨站请求及 DNS 重绑定风险。没有账号隔离、多人权限或公网部署保护；不能防御本机恶意软件。不要直接开放公网访问。

数字和技能规则检查只能发现部分异常，不能证明全部生成内容真实。人工核对原文、职位信息和生成材料后再投递。

### 发布代码前

- 检查待提交文件，排除运行数据库、密钥、真实简历和日志。
- 不要使用 `git add -f` 上传 `data/`；忽略规则不会保护已被 Git 跟踪的文件。
- 如果密钥已经泄露，应撤销 / 更换密钥；仅删除当前文件不能清除 Git 历史。
- Issue、示例和截图使用虚构数据。

## English

JobPilot is a local, single-user application. Distributed source keeps only `.gitkeep` in `data/`; sample resumes and jobs are demonstration fixtures. Do not publish your runtime database, source resume, exports or personal screenshots.

`data/jobpilot.db` stores extracted resume text, structured fields, versions, jobs, scores, documents, source snapshots, settings and logs. Uploaded PDF / DOCX binaries are not retained. The database is **not encrypted**. AI and SMTP credentials are stored there when configured; masking in API responses does not encrypt the file. Stop the service before backing up the entire `data/` directory.

External features make network requests: LinkedIn receives search keywords and locations; a configured proxy handles the connection; AI providers receive structured resume/contact data, job descriptions and baseline drafts; SMTP providers authenticate the account and deliver job digests. Connection tests, network diagnostics, proxy detection and optional dependency installation may also connect to other services. EML / mailto drafts leave the device when sent through the user's mail client.

The server binds to loopback and checks Host, Origin and write-request content type. These checks reduce cross-site request and DNS-rebinding risks, but do not authenticate local programs. There is no multi-user or internet-facing security model. Do not expose the service directly to the internet.

Number and skill checks are limited heuristics, not verification of every generated claim. Review source material and drafts before applying. Exclude personal files and credentials before publishing code; `.gitignore` does not remove tracked secrets or Git history. Revoke exposed credentials and use fictional examples in issues and screenshots.
