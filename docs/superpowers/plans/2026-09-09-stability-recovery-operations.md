# 发布一致性与恢复演练 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 发布或重启后能证明实际运行内容正确；整机故障后有可演练的独立恢复源。

**Architecture:** 复用 hot-sync、正式 release orchestrator、四表静默门禁及已有备份计划。版本观察来自部署制品和实际进程；技术恢复后再按领域证据放行业务，不新增运维控制服务。

**Tech Stack:** Bash、Docker Compose、Jenkins、PostgreSQL、systemd、rsync、现有 Python 校验工具。

**Spec:** [总计划 R5/R6/R8/R9](2026-09-09-stability-recovery-master.md)；`docs/devops/prod-release-deploy.md`；`docs/devops/integration-hot-reload.md`；[现有备份计划](2026-08-18-wes-onsite-data-recovery.md)。

## Global Constraints

- 总计划红线适用；纯局域网、单机部署，不引入集群、第三方监控服务或新的密钥治理项目。
- PostgreSQL 是持久化恢复源；Redis broker/cache 在灾难恢复时按既有计划空启动，再依据持久事实恢复，不能盲目回放旧物理命令。
- 不将 `/health`、HTTP 200、容器 running、标签 SHA 或磁盘文件 hash 单独当作进程已加载新代码的证据。
- 不以允许清理开发数据为由清理现场未闭合执行；备份、restore、重建和业务放行分别记录授权与结果。
- 不重写既有备份/运行加固任务；C3 只协调它们的独立交付与验收。尚未确定的异机地址/容量只阻塞现场部署，不阻塞代码计划。

### Task C1：闭合热更新的目标和实际加载证据

**Files:**
- Inspect/modify on proven gap: `scripts/integration-hot-sync.sh`、`scripts/integration-hot-remote.sh`、`src/celery_app/dev_reload_fingerprint.sh`、`docker-compose.integration-hot.yml`。
- Tests: `tests/deployment/test_integration_hot_sync.py`、`tests/deployment/test_celery_dev_autoreload_config.py`；现有脚本支撑优先复用。
- Create if current integration tests lack this behavior: `tests/integration/test_integration_hot_loaded_source.py`。
- Docs/config: `docs/devops/integration-hot-reload.md`、`docs/architecture/heavy-test-impact.toml`。

**Interfaces:** 保留既有 `probe/bootstrap/sync/check/disable` 子命令。扩展原 probe 输出为逐角色的实际 image revision、protected-input fingerprint、运行模式与 reload 证据；不创建新的命令家族。执行记录写入既有部署报告目录，不提交逐次日志到 Git。

**验收责任与依赖：** C1 实施 owner 负责版本及配置生效证据；配置专项验收依赖 A1 交付，不阻塞其他发布检查。现场运行负责人负责路线耗时适配，联合 WMS/ECS 提供实际时间线；该项与技术配置验收分别出结论。

- [ ] 冻结目标环境的 host/deploy-root/topology/容器角色；本地配置与远端探测不匹配时在 mutation 前失败。核验已有脚本是否已满足，满足则只补验收记录。
- [ ] 从源码同步→磁盘指纹→watcher→API/worker/Beat reload→健康探针追踪成功条件。根因假设是“磁盘更新且健康不必然等于所有相关进程已加载”；以独立 watcher 停止案例验证，不凭历史混版记录宣布当前脚本有同一故障。
- [ ] 为实际缺口建立 RED：远端错误目录；API 更新而 worker watcher 停止；新源码复制完成但旧进程仍健康；依赖或 schema 输入变化；probe 失败；总等待到期。任一未收敛不能输出成功。
- [ ] 复用已有 reload fingerprint/进程证据补最小校验；若现有机制没有任何可观测加载证据，优先由原脚本在受控发布窗口明确重启相关应用进程并验证启动，而非新增通用热更新守护服务。不得为验证而触发物理命令。
- [ ] 对 API、worker、fulfillment、Beat 按角色核对同一后端候选；前端只核对自身版本和后端合同兼容，不能要求前后端 Git SHA 相等。镜像标签与热补丁实际内容分开记录。
- [ ] **配置生效验收（C1 实施 owner）：** A1 部署后，记录实际消费 `TRANSPORT_RESULT_TIMEOUT_SECONDS` 的 API/worker 进程加载值、PID/启动时间和对应配置来源；只展示该非秘密字段。不能仅用 `.env` 文件、容器环境或 `docker exec` 新建 Python 进程的值证明长期进程已加载；复用现有进程加载证据，无法观察时按原受控入口重启并确认加载。
- [ ] **冻结期限验收（C1 实施 owner）：** 用 A1 隔离验收记录核对首次 ACCEPTED 时间与 `result_deadline_at` 的差值等于配置窗口，覆盖同步 ACK 和先到位置事实；现场只读复核正常已授权新任务的同类记录，并对照配置变更前后的原任务 deadline 保持不变。不得为取证触发额外搬运或修改记录；没有相应任务或可靠时间证据时标记该现场项未验收，不用配置读数代替。
- [ ] **路线耗时适配（现场运行负责人）：** 从正常联调的已完成任务按动作类型及路线汇总首次接纳至权威结果的样本量、p50/p95/max，单列回调延迟与异常停机；记录负责人确认的等待窗口及依据。样本不足或路线未覆盖时明确标记未确认，不宣称默认 20 分钟普遍适用，也不自动调参；调整后重新执行上述配置生效验收，原任务冻结期限不变。
- [ ] 热补丁完成后记录哪些源码已进入正式制品、哪些仍仅存在容器；只有重建后仍通过同样检查才可关闭补丁待办。健康窗口以实际探针推进，保留有界失败上限，不使用固定 sleep 当作成功。
- [ ] 运行 `uv run pytest tests/deployment/test_integration_hot_sync.py tests/deployment/test_celery_dev_autoreload_config.py -q`；在独立 Compose 项目验证 watcher 失效与重建行为。所需镜像/环境未就绪则不执行名义 skip。

**验收：** 可以明确回答“更新了什么、哪些进程实际加载、配置是否生效、重建后是否保留”；版本、配置生效、现场 deadline 复核和路线耗时适配分别记录结论及证据，未完成项不能随其他检查一并勾选。若发现 fresh readiness 缺口，复用原 probe 处理；不把 `ready=true, stale` 解释为业务已就绪。

### Task C2：承接普通 TEST FULL 发布门禁验收

**Files:**
- Reuse: `scripts/check_release_operational_readiness.py`、`src/app/runtime/orchestration/services/query/release_operational_readiness_service.py`、`src/app/runtime/orchestration/repositories/release_operational_readiness_repository.py`、`Jenkinsfile.test-deploy`。
- Existing tests: `tests/runtime/orchestration/test_release_operational_readiness_service.py`、`tests/scripts/test_check_release_operational_readiness.py`、`tests/integration/test_release_operational_readiness_postgresql.py`。
- Docs: `docs/devops/prod-release-deploy.md`；现场记录位于项目外现有 release record。

**Interfaces:** 保留四表 Service/Repository 的 `READY`、`WAIT_DRAIN`、`BLOCK` 判定与原 CLI/orchestrator；不改成通用恢复 API。此任务完整承接已归档 `2026-08-26-release-operational-readiness.md` Task 5。

- [ ] 核验当前实现与当前 release runbook。旧 Phase 10 一次性 legacy drain/absence 仅作为历史首次切换证据，不恢复旧表、旧 consumer 或兼容路径。
- [ ] 获得 TEST Deploy 授权后，在普通 BACKEND/BOTH FULL 窗口记录顺序：在线预检 READY → maintenance-stop → Nginx/API listener 关闭、Beat 停止 → worker 排空已落账工作 → 两次稳定 READY → worker stop → 必要 migration → 应用重建 → 内外部验证。在线 BLOCK 时保留当前服务，不用清理任务绕过门禁。
- [ ] `BLOCK`、`WAIT_DRAIN`、未知四表状态、不可能字段组合、查询失败、静默超时由现有隔离 PostgreSQL/pipeline 测试证明；不在共享 TEST/现场人工制造物理阻断。
- [ ] 复核 FRONTEND FULL、FAST 等独立范围仍走其现有专属路径，不为复用本步骤扩大到停所有后端或执行无关 migration。
- [ ] migration 后重建访问相关关系的长期进程，使用新会话真实查询验证；没有 schema 改动不做无关迁移。已迁移数据库不自动配回旧镜像。
- [ ] 报告候选 digest、逐角色实际代码、四表结果、部署顺序、CI 与未验收业务。一次性首次 cutover 记录不替代普通 TEST FULL 演练，技术 READY 不替代设备/供应商/业务验收。

**验收：** 原门禁的剩余现场职责完整闭合，或明确报告 DEPLOY NOT RUN；不得把历史勾选框重新描述为 fresh 结果。

### Task C3：连接既有备份计划与分层恢复手册

**Files:**
- Existing implementation owner: `docs/superpowers/plans/2026-08-18-wes-onsite-data-recovery.md`。
- Existing independent hardening owner: `docs/superpowers/plans/2026-08-18-wes-onsite-runtime-hardening.md`。
- Runbooks: `docs/devops/wes-backup-and-recovery.md` 由备份计划创建；`docs/devops/execution-recovery.md` 由 B2 创建；本任务只增加互相交接的链接和验收结果要求。
- Index: `docs/superpowers/README.md`、`docs/architecture/file_index.md`。

**Interfaces:** 使用备份计划定义的 `scripts/onsite/backup_postgresql.sh`、systemd timer、manifest/hash、异机副本和独立 restore；不新建第二个 backup 命令。两个 runbook 分别拥有“恢复数据和服务”与“恢复执行事实”。

- [ ] 重新冻结备份计划实际文件与实施状态；存在且满足目标的资产直接复用，不按旧清单重复创建。更新其过时基线说明，保留唯一实现 owner。
- [ ] 现场实施前取得真实异机目录、容量/保留策略、连接条件、失败通知接收方式与独立恢复环境；不在仓库提交秘密或实际 dump。
- [ ] 按原备份计划完成小时级 custom dump、配置/制品关联、SHA-256、异机副本与真实 restore；仅 `pg_restore --list` 或同机副本不能验收。保留原 RPO ≤ 1 小时、RTO ≤ 2 小时目标，测量失败则报告实测差距。
- [ ] restore 后先禁止新物理派发，恢复匹配应用与空 Redis，核对任务/Evidence/资源状态；再按 B2 手册等待 WMS/ECS 权威事实，符合领域准入后才放行。数据库备份时刻早于外部物理事实，不能直接把备份位置当当前现场位置。
- [ ] 运行加固计划只处理已有明确触发的 Beat、Redis/Nginx、PostgreSQL问题；它不阻塞基础代码查询验收，不把全部加固拼入同一重启窗口。
- [ ] 组织独立工程师执行服务重启与整机恢复两类演练，分别记录技术恢复时间、待对账对象、外部确认等待、业务放行时间。
- [ ] 完成并失去职责的过程计划在验收事实全部移交后外部归档；更新当前索引，不删除硬件厂商输入，不保留 `.old` 或兼容脚本。

## 完成门禁

- [ ] C1 只有实际行为变更才运行相应脚本测试/QUALITY/selector HEAVY；C2 复用仍有效的同快照证据。
- [ ] 无新 schema 不创建迁移；数据恢复不实现历史版本转换。
- [ ] 各门禁报告区分 IMPLEMENTED、TECHNICALLY VERIFIED、DEPLOYED、PHYSICAL CONFIRMED、BUSINESS ACCEPTED。
- [ ] 本轮仅制定计划；尚未获得异机输入或部署授权不能报告现场灾难恢复闭环。

## 当前实施核验

2026-09-09 基线为 develop `9e2104713ecd181e7eefa5bea133efcaf11a8a21`，实施 worktree 为
`codex-stability-recovery`。C1 原 `activate` 在源码同步完成后已经调用 Compose restart，失败退出；
原测试覆盖 restart 顺序/失败、protected input 拒绝与逐角色 probe。相关部署/重载和 C2 门禁聚焦测试共 64 项通过，
本切片未修改部署脚本。独立 Compose 加载实验和现场长期进程配置证据尚未取得，不能用脚本 Mock 替代。

C2 普通 TEST FULL 尚未运行：DEPLOY NOT RUN。C3 目标脚本 `scripts/onsite/backup_postgresql.sh`、
对应 systemd 配置及 `docs/devops/wes-backup-and-recovery.md` 尚不存在。它们仍由原备份计划独立交付，
本切片没有创建第二套备份实现，也没有可供声明通过的异机副本/restore/RPO/RTO 证据。
执行恢复侧交接已写入 [execution-recovery](../../devops/execution-recovery.md)。
