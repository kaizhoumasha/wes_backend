# 前后端独立发布与方向性兼容设计

**状态：** APPROVED TARGET

**日期：** 2026-08-25

**范围：** `/Users/kaizhou/codeDev/wes_backend`、`/Users/kaizhou/codeDev/wes_frontend` 与既有 TEST/现场发布编排

## 1. 问题

当前前端流水线只有在提供固定后端镜像、后端 Commit 和部署源码 Commit 时才发布镜像，并在发布成功后直接触发 TEST 部署。部署门禁又要求前端镜像声明的后端契约 Commit 与所选后端 Commit 精确相等。即使多个后端 Commit 导出的 OpenAPI 和权限事实完全相同，前端仍需要重新冻结 provenance 并重新构建；前端构建、镜像发布和环境部署因此被绑定成一次成对操作。

问题不是保留审计信息，而是把“来源身份相等”误当成“消费者与提供者兼容”。Commit、tree 和镜像 digest 应继续证明制品身份，但不能替代方向性兼容判断。

## 2. 目标

- 前端、后端和发布检查器分别构建、测试和发布不可变镜像；任何 producer 构建都不自动触发环境部署。
- 独立发布作业按 `FRONTEND`、`BACKEND` 或 `BOTH` 选择候选；单侧发布只输入该侧候选 digest，另一侧从现场运行容器与最近有效发布证据自动发现并交叉验证。
- 兼容方向固定为“所选前端实际需求 → 所选后端实际能力”，不比较前后端 Commit 是否相等。
- 日常无 migration、依赖、运行配置、Compose、OpenAPI、权限或生产镜像 recipe 变化的发布进入 FAST；其余进入 FULL。
- 菜单继续由前端静态路由唯一拥有；发布基础能力不得恢复菜单数据库、跨镜像菜单清单或菜单同步脚本。

## 3. 非目标

- 不建设发布数据库、动态规则引擎、契约注册中心、菜单服务、配置中心或事件总线。
- 不为旧 exact-commit 门禁保留开关、双轨、shim、fallback 或兼容 wrapper。
- 不自研 OpenAPI breaking-change、schema 裁剪或 `$ref` 遍历引擎。
- 不把 WMS/ECS 任务、`DELIVERY_UNKNOWN`、设备动作、Callback、物理完成或业务验收塞进基础 release checker。
- 不为纯局域网部署新增签名 PKI；继续使用受控 Registry、digest、OCI labels 和 SHA-256。
- 不承诺所有 FULL 发布小于 10 分钟；≤10 分钟目标只适用于 FAST，FULL 使用明确维护窗口。

## 4. 所有权与依赖方向

### 4.1 Backend producer

- 后端 FastAPI 路由和 `create_app().openapi()` 是 provider OpenAPI 真源。
- `src/utils/permission_scanner.py` 的经校验权限叶子是 provided permissions 真源；派生分组不进入发布合同。
- 后端镜像内置 provider OpenAPI、provided permission leaves、expected Alembic head 和生产输入指纹。
- 后端应用镜像不包含 checker，也不声明所需 checker 版本。

### 4.2 Frontend producer

- `contracts/openapi.current.json` 和新增的 `contracts/permissions.current.json` 是普通前端构建的离线 canonical 输入。
- 只有显式 `contract:freeze` 从干净后端 checkout 调用 backend exporter，并以原子方式刷新两份快照及 provenance；普通 build/test/verify 不访问后端。
- 前端从生产源码实际消费生成排序后的 required operations 和 required permission names；不把整份导入模块或整份后端权限目录算作需求。
- 前端镜像内置 consumer OpenAPI 基线、required operations、required permissions 和生产输入指纹；不记录“目标后端 Commit”。

### 4.3 Release checker

- checker 源码位于后端仓库独立 `tools/release_checker/`，有独立依赖、测试、Dockerfile 和 Jenkins job，不导入后端 `src/` 或前端代码。
- checker 仅在部署前读取两个已固定镜像的原始制品和部署输入；前端、后端运行时都不调用 checker。
- checker 镜像 digest 只由部署源码固定。checker 升级不要求重建前端或后端镜像。
- OpenAPI breaking-change 复用固定版本 `oasdiff`；权限兼容只做集合包含判断。

### 4.4 Release orchestrator

- 独立发布作业是环境变更唯一入口。Producer CI 只发布镜像，不调用部署 job。
- 编排负责候选 digest 固定、当前对端发现、revision/source identity、内容指纹比较、FAST/FULL、checker、既有业务预检、维护态、切换、readiness、拓扑和发布证据。
- `FRONTEND` 只接受 frontend candidate，`BACKEND` 只接受 backend candidate，`BOTH` 才接受两侧 candidate。单侧作业不得要求操作者重新选择、重新批准或重建当前对端。
- 当前对端必须同时满足 live container digest 与最近成功发布证据一致；缺失或不一致在维护前阻断。发布作业继续禁止并发，并在进入维护前再次读取当前对端 digest，防止预检后漂移。
- WMS/ECS/活动任务预检保留为独立阶段；它依赖发布编排，但不进入 checker 包或其单元测试。

依赖方向只有：

```text
frontend image ----\
                    > release-checker (deploy-time read only)
backend image -----/

deploy source ----> pinned checker digest
```

不存在 frontend/backend/checker 的运行时循环依赖。

### 4.5 状态术语

- `BUILD_VERIFIED`：producer 自身测试、导出和镜像构建通过，尚未推送。
- `PUBLISHED`：不可变镜像 digest 已推送并完成自身 artifact/label 校验，仍未改变任何环境。
- `DEPLOYED`：独立发布作业已用候选与当时现场对端完成门禁和 cutover。

Producer 只能产生 `BUILD_VERIFIED` 或 `PUBLISHED`。前端发布成功不表示后端已部署，后端发布成功也不表示前端已部署；只有 release orchestrator 可以产生 `DEPLOYED` 证据。

## 5. 原始发布制品

不增加复合 consumer/provider manifest。镜像使用稳定路径保存原始 JSON，OCI labels 保存对应 SHA-256 和运行输入指纹。下列结构和 label 名是字节级机器合同；实现不得再发明别名、宽松 reader 或第二种格式。

### 5.1 Backend image

- `/opt/wes/release/provider-openapi.json`
- `/opt/wes/release/provided-permissions.json`：`{"kind":"wes.release.provided-permissions.v1","permissions":[...]}`。每个 permission 必须且只能包含 `name`、`type`、`category`、`description`、`resource`、`action`、`method`、`path`；按 `(name,type,method,path)` 排序，重复 `name` 即拒绝。兼容检查只读取 `name`，完整事实用于 required operations 派生与审计。
- OCI labels：`org.wes.release.provider-openapi.sha256`、`org.wes.release.provided-permissions.sha256`、`org.wes.release.migration-tree.sha256`、`org.wes.release.backend-dependencies.sha256`、`org.wes.release.backend-recipe.sha256`、`org.wes.release.expected-schema-head`。

### 5.2 Frontend image

- `/opt/wes/release/consumer-openapi.json`
- `/opt/wes/release/required-operations.json`：`{"kind":"wes.release.required-operations.v1","operations":[{"method":"GET","path":"/api/..."}]}`；method 大写，按 `(path,method)` 排序且唯一。
- `/opt/wes/release/required-permissions.json`：`{"kind":"wes.release.required-permissions.v1","permissions":["..."]}`；名称按 Unicode code point 排序且唯一，超级用户哨兵 `*` 不进入集合。
- OCI labels：`org.wes.release.consumer-openapi.sha256`、`org.wes.release.required-operations.sha256`、`org.wes.release.required-permissions.sha256`、`org.wes.release.frontend-dependencies.sha256`、`org.wes.release.frontend-recipe.sha256`。

### 5.3 Strict parsing and hashing

- 三个带 `kind` 的 JSON 合同拒绝未知顶层字段、未知条目字段、缺字段、空字符串、重复项、非规范排序和错误 `kind`；没有兼容读取路径。
- OpenAPI 必须是自包含的 JSON 文档；checker 禁止外部 `$ref` 和网络解析。
- 所有 `*.sha256` label 是对应镜像内文件原始字节的 lowercase hex SHA-256，不是重新序列化后的对象 hash。依赖、migration tree 与 recipe 输入组统一编码为 `{"kind":"wes.release.input-set.v1","files":[{"path":"repo/relative/path","sha256":"..."}]}`；files 按 path 排序，path 必须是无 `..` 的仓库相对 POSIX 路径，label 是该规范 JSON 原始字节 hash。expected schema head 是实际 Alembic 单 head revision。
- Backend CI-only `provider-fingerprints.json` 必须且只能包含 `kind=wes.release.backend-fingerprints.v1`、`provider_openapi_sha256`、`provided_permissions_sha256`、`migration_tree_sha256`、`dependencies_sha256`、`recipe_sha256`、`expected_schema_head`。Frontend CI-only `consumer-fingerprints.json` 必须且只能包含 `kind=wes.release.frontend-fingerprints.v1`、`consumer_openapi_sha256`、`required_operations_sha256`、`required_permissions_sha256`、`dependencies_sha256`、`recipe_sha256`。除 `kind` 和 schema head 外均为 64 位 lowercase hex。
- 既有 revision/source labels 保留原名用于制品追踪，不参与跨镜像兼容。

### 5.4 Determinism

- JSON 使用 UTF-8、稳定键顺序、稳定数组顺序和结尾换行；同一输入重复导出必须字节一致。
- 镜像内文件 SHA-256 必须与自身 OCI label 一致；外部参数不得覆盖或伪造镜像自带事实。
- revision、source tree 和冻结来源 Commit 继续保留作 provenance，但不参与跨镜像兼容结果。

## 6. Consumer surface

### 6.1 Required permissions

- 路由 `meta.permission`/`meta.permissions`、CRUD 配置和自定义 action/dialog/guard 中的显式权限叶子组成前端需求集合。
- `permissions: SOME_GROUP` 和动态索引不是可接受的发布合同。现有页面改为显式 `CrudPagePermissionConfig` 叶子映射；exporter 遇到整组或无法解析引用时 fail closed。
- 不在 exporter 中复制 CRUD feature/action 推理，也不增加手工权限 registry。

### 6.2 Required operations

Required operations 是以下来源的确定性并集：

1. required permission leaves 在 canonical permission snapshot 中对应的 method/path；
2. token refresh、SSE 等生产源码中的直接 endpoint 字面量；
3. 生产源码实际调用的 generated `*ApiMethods.method()`，其 method/path 映射复用现有 API generator 纯函数。

动态 method/path 或无法解析的生产调用使导出失败。禁止扫描 dist、按导入 module 全量推断、维护第二份 schema 清单或手写 OpenAPI `$ref` 裁剪器。

Exporter 使用项目现有 TypeScript compiler API 解析生产源码；禁止正则扫描。别名 import、解构、可选链和静态计算属性必须能确定解析，动态调用、无法解析的 barrel 转出或生产根目录外调用必须 fail closed。

Checker 先把 consumer/provider 各投影为只含全部 required operations 的 OpenAPI 视图，再只启动一次 `oasdiff`，让其负责请求、响应和传递 schema；禁止按 operation 循环启动子进程。实现前必须用固定版本 fixture 证明：未使用 endpoint/schema 变化不报告；同一路径未使用 HTTP method 的破坏不报告；选中 operation 删除或其传递 schema 破坏会报告；外部 `$ref` 被拒绝。若固定工具无法精确做到 method 级过滤，只允许评估一个成熟 OpenAPI filter，并重新审查设计，禁止手写 `$ref` walker。

## 7. 方向性兼容规则

1. 固定候选 digest；从部署源码固定 checker digest。单侧发布从 live container 与最近有效证据发现当前对端 digest，交叉验证一致后读取两侧 revision 与原始制品。
2. 校验所有原始制品 SHA-256 与镜像 label；缺失、为空、格式非法或不一致时在维护前失败。
3. 验证 `required permission names ⊆ provided permission names`；描述、分类等元数据不参与兼容判定。
4. 仅对 required operations 的两份投影视图运行一次固定版本 `oasdiff`，并禁用外部引用。
5. `ERR` 自动阻断。
6. `WARN` 默认暂停；授权发布操作人必须填写理由。豁免只绑定 frontend digest、backend digest、checker digest 和 diff 摘要 hash，任一变化即失效，不要求双人审批。
7. 生成确定性的 `compatibility-report.json`，保存到 `/srv/wes/releases/${RELEASE_ID}/` 并归档为 Jenkins artifact；不建立发布数据库。

报告顶层必须且只能包含：`kind`（固定 `wes.release.compatibility-report.v1`）、`release_id`、`deploy_scope`、`candidate_digests`、`current_digests`、`checker_digest`、`artifact_hashes`、`auto_mode`、`effective_mode`、`mode_reasons`、`compatibility`、`approval`、`pre_cutover_state`。digest map 只含 `frontend`/`backend`；单侧 candidate map 只出现候选侧，current map 记录预检时两侧现场 digest。`artifact_hashes` 必须且只能是 frontend 的 `consumer_openapi|required_operations|required_permissions` 与 backend 的 `provider_openapi|provided_permissions` 两个严格对象。`auto_mode/effective_mode` 只能是 `FAST|FULL`，`mode_reasons` 是排序且唯一的字符串数组。`compatibility` 必须且只能包含 `status`、`diff_hash`、`findings`；status 只能是 `PASS|WARN|ERR`，每个 finding 必须且只能包含 `code`、`severity`、`location`、`message` 并按 `(severity,code,location,message)` 排序。`approval` 为 `null`，或必须且只能包含 `frontend_digest`、`backend_digest`、`checker_digest`、`diff_hash`、`reason`。`pre_cutover_state` 只能是 `READY|PRE_CUTOVER_ABORTED`。未知字段、未知枚举或 digest 绑定不完整一律拒绝。

Checker 典型目标 ≤30 秒，硬超时 60 秒。超时或异常是 `PRE-CUTOVER_ABORTED`，不改变当前运行环境。

## 8. FAST/FULL 分类

分类比较“当前部署证据 → 候选制品/有效配置”，不使用候选 Commit 的 changed paths 作为最终真源。

以下任一指纹变化或证据缺失都自动进入 FULL：

- Backend：migration tree、生产 Dockerfile 实际消费的依赖输入、provider OpenAPI、provided permissions、生产 image recipe/entrypoint。
- Frontend：`package.json`/`pnpm-lock.yaml`、consumer OpenAPI、required operations、required permissions、生产 Dockerfile/Nginx 配置。
- Deploy：实际 TEST/PROD Compose、cutover 脚本和其声明的运行配置文件 SHA-256。
- Runtime：现场 DB head、有效 `.env` 和 WMS provider profile 等批准配置的 hash；记录 hash，不记录秘密内容。

测试、文档、CI-HEAVY Compose 和前端独立 preview Compose 不进入发布模式指纹。HEAVY selector 仍只拥有测试选择，不能成为 release mode 真源。

系统只提供 `FORCE_FULL`。操作者可以把自动 FAST 升级为 FULL，但不存在 `FORCE_FAST`。首个基线、上一版证据缺失或任何读取异常均为 FULL。

FAST 要求现场 DB head 与候选 expected head 精确一致。FULL 只允许从已知祖先向前迁移；多 head、未知 revision、倒退或分叉直接阻断。

## 9. Cutover 状态与回滚

- 构建、pull、digest pin、指纹、compatibility 和业务预检全部在维护前完成。
- 单侧发布在维护前再次验证当前对端仍等于报告中的 digest；变化则以 `PRE-CUTOVER_ABORTED` 结束并重新预检，禁止沿用旧报告。
- 维护前失败：`PRE-CUTOVER_ABORTED`，当前环境完全不动。
- 进入维护后失败：`CUTOVER_FAILED_MAINTENANCE_HELD`，外部入口保持关闭。
- FAST 只切发生变化的一侧。前端变化不重建后端；后端变化必须让 API、Celery、WMS fulfillment、Beat 和 Flower 使用同一 backend digest，禁止混合版本。目标服务中断 ≤2 分钟。
- FULL 在维护态执行备份、已批准 forward migration、完整重建和数据库真实查询。
- migration 后禁止无条件自动切回旧镜像。失败后保持维护态并 forward-fix，或恢复已验证数据库备份和先前镜像；开发/测试清理重建需要单独授权。

## 10. 初始切换与菜单收敛

新门禁不兼容旧标签路径，也不保留双轨：

1. 在当前功能基线上完成 backend/frontend raw artifacts、checker 和三个独立 Jenkins job。
2. 分别发布一次包含新制品的前端、后端基线镜像；两次 producer publication 互不等待，也不触发环境部署。
3. 启用新部署门禁，同步删除 exact backend Commit equality、旧参数、旧测试和 frontend auto-deploy。
4. 菜单收敛的新前端以独立 FULL 发布到当前后端；验证浏览器不再调用菜单 API。
5. 菜单收敛的新后端再以独立 FULL 发布并执行不可逆菜单 migration。

允许的菜单阶段组合是“新前端 + 旧后端”和“新前端 + 新后端”；“旧前端 + 新后端”必须被 consumer compatibility gate 阻断。这里的顺序依赖是合同事实，不是 Commit 配对。

## 11. 证据边界

- Producer 单元/合同测试证明导出确定性、静态消费提取和镜像身份。
- Checker 测试证明方向性 OpenAPI、权限超集、WARN/ERR、超时和报告。
- Cutover simulation 证明 FAST/FULL、维护前/后失败、服务原子性和入口恢复顺序。
- 菜单浏览器 QA 证明前端路由投影与当前用户权限组合。
- 上述证据不证明供应商协议一致、真实 WMS/ECS callback loop、设备物理完成或业务验收。

## 12. 完成条件

- 前端和后端 `develop` producer 分别在对端仓库、对端镜像和部署作业不可用时仍可发布自身不可变镜像，且均不自动部署。
- 不同 Commit 的兼容镜像可以通过；破坏 required operation 或缺少 required permission 必须在维护前失败。
- 未使用 endpoint/schema 变化不产生兼容错误，但 OpenAPI/权限任何变化仍把发布分类为 FULL。
- FAST/FULL 由 candidate-vs-deployed 内容与有效配置决定，且只能人工升级为 FULL。
- checker、producer 和 deploy job 可独立构建、运行和测试，无运行时三方循环依赖；单侧部署不要求人工输入当前对端候选。
- 旧 paired-release 参数、commit equality、frontend auto-deploy 和跨镜像菜单同步全部删除。
- 当前 Runbook、菜单 spec/plan、Jenkins 测试和发布记录只描述新路径；已完成历史计划明确标注其 paired-release 边界已被本设计取代。
