from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRS = ROOT / "docs/architecture/SRS.md"
ADR = ROOT / "docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md"
SPEC = ROOT / "docs/superpowers/specs/2026-06-06-wes-single-layer-rack-orchestration-boundary-spec.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalize(text: str) -> str:
    return " ".join(text.split())


def assert_contains_any(text: str, choices: tuple[str, ...]) -> None:
    normalized = normalize(text)
    assert any(choice in normalized for choice in choices)


def assert_contains_all(text: str, choices: tuple[str, ...]) -> None:
    normalized = normalize(text)
    for choice in choices:
        assert choice in normalized


def assert_not_contains_any(text: str, choices: tuple[str, ...]) -> None:
    normalized = normalize(text)
    for choice in choices:
        assert choice not in normalized


def nearby_text(text: str, phrase: str, radius: int = 700) -> str:
    index = text.find(phrase)
    assert index >= 0
    return text[max(0, index - radius) : index + len(phrase) + radius]


def test_srs_states_wes_does_not_directly_dispatch_rcs_agv_ctu():
    srs = read(SRS)

    assert_contains_any(srs, ("WES 不直连 RCS", "WES 不直接调用 RCS"))
    assert_contains_any(
        srs,
        (
            "AGV/CTU/RCS 任务由 WES 生成业务需求并提交 WMS，由 WMS 转发执行",
            "由 WMS 转发给 RCS/AGV/CTU",
            "WMS 转发 RCS/AGV/CTU",
        ),
    )
    assert_contains_any(srs, ("不直接下发 AGV/CTU 任务", "不由 WES 直连"))


def test_srs_keeps_single_layer_snapshot_as_wes_authority_only():
    srs = read(SRS)

    assert "单层货架 active 执行快照" in srs
    assert_contains_any(
        srs,
        (
            "本策略仅用于当前单层货架 active 执行快照内",
            "WES 可以持有执行事实、单层货架 active 执行快照、运行投影、WMS 回调和对账证据",
        ),
    )
    assert_not_contains_any(srs, ("单层货架全局主账", "全局货架管理由 WES", "WES 管理所有货架"))


def test_srs_keeps_non_single_layer_inventory_authority_in_wms():
    srs = read(SRS)

    assert_contains_any(
        srs,
        (
            "库存、货架资源与 RCS 调度权仍由现有 WMS 持有",
            "库存主账由 WMS 持有",
            "库存属性、库存转移和账务确认由 WMS 完成",
        ),
    )
    assert_contains_any(srs, ("非单层资源授权", "五层货架空箱资源"))
    assert_not_contains_any(
        srs,
        (
            "WES 作为库存主账",
            "WES 维护库存主账",
            "WES 本地锁定 `Empty_Bin`",
            "WES 自动扣减库存",
        ),
    )


def test_srs_keeps_empty_single_layer_rack_authority_in_wms():
    srs = read(SRS)

    assert "空架资源主账" in srs
    assert_contains_any(srs, ("物理占用仍由 WMS/RCS 持有", "物理库位权威"))
    empty_rack_phrases = (
        "WES 监控装箱区",
        "可用空单层货架",
        "可执行空单层货架",
        "判断装箱区是否已有",
    )
    matched_phrases = [phrase for phrase in empty_rack_phrases if phrase in srs]
    assert matched_phrases
    for phrase in matched_phrases:
        context = nearby_text(srs, phrase)
        assert_contains_all(context, ("active 执行快照", "WMS 授权", "回调证据"))
        assert_contains_any(
            context,
            (
                "不代表 WES 接管全局空架库存或物理库位权威",
                "空架资源主账和物理占用仍由 WMS/RCS 持有",
            ),
        )


def test_srs_defines_workline_start_as_ready_admission_not_job_start():
    srs = read(SRS)
    context = nearby_text(srs, "WORKLINE_START_REQUESTED", radius=240)

    assert_contains_all(
        context,
        (
            "WORKLINE_START_REQUESTED",
            "工作线进入 READY/待机状态",
            "可以开始接收业务需求",
        ),
    )
    assert_contains_all(
        context,
        (
            "不表示已有货架到位",
            "不表示立即开始分拣",
        ),
    )


def test_srs_defines_sorter_ng_place_on_target_arm():
    srs = read(SRS)
    context = nearby_text(srs, "NG 放置动作", radius=240)

    assert_not_contains_any(
        srs,
        (
            "NG_ARM 对应",
            "NG_ARM 作为",
            "NG_ARM 目标设备角色",
            "NG_ARM 机械臂",
            "NG_ARM 执行",
        ),
    )
    assert_contains_all(
        context,
        (
            "SOURCE_ARM",
            "TARGET_ARM",
            "NG 放置动作由 `TARGET_ARM` 完成",
            "ROLE_SORTING_TARGET_ARM",
        ),
    )


def test_srs_defines_return_rack_as_execution_view_not_inventory_master():
    srs = read(SRS)

    assert "退料执行投影或证据视图" in srs
    assert "Return_Rack_Inventory" not in srs
    assert_contains_any(srs, ("退料库存主账由 WMS 持有", "库存增加在 WMS 确认后生效"))


def test_srs_keeps_production_and_return_rack_slots_as_execution_evidence():
    srs = read(SRS)

    assert_contains_any(srs, ("PKG、Rack_ID、Side、Slot_ID 执行投影或证据",))
    assert_contains_any(srs, ("真实储位归属、库存可用性和 A/B 面资源授权以 WMS 为准",))
    assert_contains_any(srs, ("不作为生产货架或退货货架 A/B 面真实占用主账", "Side/Slot 授权以 WMS 为准"))
    assert_not_contains_any(
        srs,
        (
            "WES 主账管理生产货架",
            "WES 主账管理退货货架",
            "真实储位归属由 WES",
            "库存可用性由 WES",
            "A/B 面资源授权由 WES",
        ),
    )


def test_srs_does_not_assign_five_layer_hot_cold_or_side_balance_to_wes():
    srs = read(SRS)

    assert_contains_any(srs, ("五层货架冷热区、A/B 面负载、空箱授权和 CTU 路径由 WMS/RCS 作为权威系统判断",))
    assert_contains_any(srs, ("不动态平衡五层货架两侧真实负载", "不得本地触发五层货架资源授权"))
    assert_not_contains_any(
        srs,
        (
            "WES 动态平衡五层货架",
            "WES 判断五层货架冷热区",
            "WES 授权五层货架空箱",
            "WES 规划 CTU 路径",
        ),
    )


def test_outbound_is_driven_by_order_wave_or_line_demand():
    srs = read(SRS)
    context = nearby_text(srs, "#### 3.3.3 SMT 生产发料协调", radius=900)

    assert_contains_any(context, ("SAP 工单", "工单"))
    assert_contains_any(context, ("生成波次", "滚筒波次", "波次"))
    assert_contains_any(context, ("产线需求", "SFC 的 `Magazine_Request`"))
    assert_contains_any(context, ("WES 生成搬运需求并提交 WMS", "搬运需求并提交 WMS"))


def test_outbound_exception_does_not_auto_deduct_inventory():
    srs = read(SRS)
    context = nearby_text(srs, "Pick_Fail", radius=360)

    assert_contains_all(
        context,
        (
            "Pick_Fail",
            "库存扣减",
            "必须由 WMS 确认或授权",
            "WES 不自动扣减库存",
        ),
    )


def test_return_flow_keeps_lcr_xray_labeling_execution_evidence():
    srs = read(SRS)
    context = nearby_text(srs, "### 3.6 生产退料闭环", radius=3600)

    assert_contains_any(context, ("LCR 测试决策引擎", "LCR"))
    assert_contains_any(context, ("X-Ray 智能清点与贴标", "X-Ray"))
    assert_contains_any(context, ("贴标", "Relabeling"))
    assert_contains_any(context, ("执行结果和证据", "执行证据", "检测、贴标和执行证据"))


def test_return_flow_keeps_inventory_confirmation_in_wms():
    srs = read(SRS)
    context = nearby_text(srs, "#### 3.6.4 退料入库与库存更新", radius=520)

    assert_contains_all(
        context,
        (
            "WMS 完成库存调整",
            "回传确认",
            "SAP 同步",
        ),
    )
    assert_contains_any(context, ("库存增加在 WMS 确认后生效", "由 WMS 负责向 SAP 推送退料数据"))


def test_transfer_supply_and_empty_rack_return_are_wms_transport_demands():
    srs = read(SRS)
    transfer_context = nearby_text(srs, "#### 3.5.4 机构件物流协同", radius=1400)
    return_context = nearby_text(srs, "空架回流", radius=360)

    assert_contains_any(transfer_context, ("Transport_Task", "搬运需求"))
    assert_contains_any(transfer_context, ("提交 WMS", "由 WMS 转发 RCS 执行"))
    assert_contains_any(return_context, ("WMS 自主调度", "提交 WMS"))
    assert_contains_any(srs, ("所有搬运、交换、旋转需求均通过 4.1 的 WMS 接口提交", "搬运/交换/旋转需求均通过"))


def test_srs_allows_execution_snapshots_without_inventory_mastership():
    srs = read(SRS)

    assert_contains_any(srs, ("WES 不持有库存主账", "WES **不维护库存主数据**"))
    assert_contains_any(
        srs,
        (
            "WES 可以持有执行事实、单层货架 active 执行快照、运行投影、WMS 回调和对账证据",
            "WES 可使用自身保存的执行事实、单层货架 active 执行快照、运行投影和回调证据恢复执行上下文",
        ),
    )
    assert_not_contains_any(srs, ("WES 不持有数据", "WES 不保存任何数据"))


def test_adr_and_spec_keep_wms_as_transport_and_inventory_authority():
    adr = read(ADR)
    spec = read(SPEC)
    combined = adr + "\n" + spec

    assert_contains_all(
        adr,
        (
            "WMS 是库存、预留、扣减、账务、SAP 同步和空箱资源授权的唯一权威",
            "WES 不锁定五层货架空箱",
            "WES 只提交 `FULL_BIN_EXCHANGE` 外部请求，等待 WMS/RCS 回调",
        ),
    )
    assert_contains_all(spec, ("WMS 是非单层资源和库存权威", "AGV/CTU/RCS", "WMS 转发"))
    assert_not_contains_any(
        combined,
        (
            "WES 直连 RCS",
            "WES 直接下发 AGV",
            "WES 直接下发 CTU",
            "WES 扩展成全局 Location",
            "WES 扩展成全局库存主账",
        ),
    )


def test_spec_keeps_single_layer_snapshot_scope_bounded():
    spec = read(SPEC)

    assert_contains_any(spec, ("WES 本阶段只对 `SINGLE_LAYER` 单层货架的 active 执行快照拥有本地权威",))
    assert_contains_any(spec, ("该快照用于执行恢复、设备指令、对账和诊断，不作为库存或资源主账",))
    assert_contains_any(spec, ("不扩展为全局货架管理", "不能作为库存可用性判断的本地主账"))
    assert_not_contains_any(spec, ("全局货架资源主账", "所有货架 active 快照"))
