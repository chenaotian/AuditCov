# AuditCov

此文件是 [SKILL.md](SKILL.md) 的中文同步译文，仅供项目维护者阅读。正式给 Codex 使用的 skill 文件仍然是英文版 `SKILL.md`。

## 元数据

- `name`: `auditcov`
- `description`: 仅当用户明确点名 AuditCov、AuditCov skill、AuditCov MCP、审计覆盖率、客观读取覆盖率，或者要求代码审计达到某个具体覆盖率阈值时使用。普通代码审计、安全审计、漏洞挖掘请求如果没有提到覆盖率或 AuditCov，不要使用。

## AuditCov

AuditCov 用来记录客观读取覆盖率：也就是通过 AuditCov MCP 读取工具完整返回给模型的源码行。它不能证明漏洞审计已经完成，也不能证明模型已经理解了每一行返回的代码。

## 触发规则

只有当用户明确要求使用 AuditCov skill 或 AuditCov MCP 时，才使用 AuditCov。

以下情况才算明确触发：

- 用户点名 `AuditCov`、AuditCov skill，或者 AuditCov MCP。
- 用户要求统计审计覆盖率、客观读取覆盖率，或者代码审计覆盖率。
- 用户要求审计直到达到某个具体覆盖率阈值，例如 80%。

普通安全审计、代码审计、漏洞挖掘或仓库探索请求，如果用户没有明确要求使用 AuditCov 或审计覆盖率，不要使用 AuditCov。

## 初始化规则

当前请求首次在线程中触发 AuditCov 时，只调用一次 `auditcov_init_project`。不要为了刷新状态、重置覆盖率或改善分母而重复调用。如果当前线程已经初始化过 AuditCov，继续使用已有项目。只有当用户明确开始一个新的 AuditCov 审计范围时，才重新初始化。

初始化之后，不要为了更容易达到覆盖率而缩小或替换目标路径。

## 覆盖率目标规则

如果用户明确要求审计直到达到某个覆盖率目标，例如审计到 80% 覆盖率，或者确保审计覆盖率达到 80%，需要创建 goal，并持续审计直到该目标完成。如果用户没有要求具体覆盖率目标，只把覆盖率作为参考信息，审计节奏按正常方式推进。

当用户要求达到具体覆盖率阈值时：

1. 在开始审计前创建 goal，目标可以写成类似：`审计目标代码，直到 AuditCov 客观读取覆盖率至少达到 80%，并报告安全发现`。
2. 按用户确认的审计范围初始化 AuditCov。
3. 持续通过 `auditcov_read_file` 读取目标文件、分析返回的代码，并用 `auditcov_get_coverage` 检查覆盖率，直到达到用户要求的阈值。
4. 只有在覆盖率阈值实际达到，或者遇到真实阻塞无法继续推进时，才能结束该 goal。

如果用户没有要求具体阈值，只把覆盖率作为参考信号。不要把覆盖率变成隐含的完成门槛。

## 代码读取规则

AuditCov 激活后，凡是为审计分析或覆盖率统计提供源码内容的文件读取、行范围读取，都要通过 `auditcov_read_file`。不要用 `cat`、`type`、`Get-Content`、`sed -n`、`head`、`tail`、`less` 等 shell 命令或其它工具倾倒源码文件或行范围，来替代 `auditcov_read_file`。

允许搜索代码。shell 命令和搜索工具可以打印匹配行或小片段，用来定位候选文件、函数、符号或模式。搜索输出不计入 AuditCov 覆盖率；如果要把搜索到的代码区域作为审计证据使用，需要再通过 `auditcov_read_file` 读取相关文件或范围。

## MCP 工作流

只有通过 AuditCov MCP 工具读取到的内容，才能计入客观覆盖率：

- `auditcov_init_project`：冻结当前线程的覆盖率分母。
- `auditcov_read_file`：读取完整源码行，并记录客观读取覆盖率。
- `auditcov_get_coverage`：查看项目、目录或文件覆盖率。
- `auditcov_get_file_detail`：查看单个文件中已覆盖和未覆盖的行号范围。

推荐流程：

1. 根据用户请求确定仓库根目录和目标路径。不要为了提高覆盖率而缩小目标分母。
2. 在 AuditCov 首次激活时，对选定范围调用一次 `auditcov_init_project`。
3. 按需使用 shell 命令做发现和代码搜索。搜索片段不计入覆盖率。
4. 对需要计入覆盖率的源码使用 `auditcov_read_file`。如果返回结果被截断，从 `next_start_line` 继续读取。
5. 使用 `auditcov_get_coverage` 和 `auditcov_get_file_detail` 选择剩余未读文件或行号范围。
6. 汇报覆盖率时称为客观读取覆盖率，不要把它说成审计完成的证明。

如果 AuditCov MCP 工具不可用，明确说明当前 Codex 环境没有配置 AuditCov。不要假装 shell 读取也能计入 AuditCov 覆盖率。
