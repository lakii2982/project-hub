# 核算内核契约缺口的独立规划

本规划起点579e60e；实际可执行状态以合入后的可信BASE为准。PH140已通过PR89最终3224dd2当前全部11检查、无未解决讨论并正常合入46f9cb0（仅迁移扫描/兼容重放范围），PH141仍proposed，不能因140完成自动晋级。前置为独立只读DoR预审；下述缺口不是已实现能力。

本规划拟创建PH210（S/docs，依赖PH209）做两个最小canonical契约修正；创建PH211接续纯规划槽；PH141依赖保留140并增加210，status继续proposed，之后仍需211核对来源授权映射、期间适配、持久模型/唯一事务owner的DoR。不得用PH210完成代替141的ready许可。

PH210精确目标：
1. 在实际定义catalog-completeness.schema.json的AccountingEvent加入server-only可空reverses_event_ref，非空只许core/accounting_event不可变引用；由服务生成、同企业/原事件授权/版本/期间/完整冲销唯一性继续检查。原事件不更新。relationships.event-reversal保留AccountingEvent embedded_reference，其桥输出只是该关系只读投影：left原事件、right新冲销事件，id/version/created_at从冲销事件派生，不新增可编辑桥或双关系。无AccountingEvent的旧账仍走legacy adapter/原冲销服务，不合成历史事件。DTO不接受该字段；readOnly标签不等于服务授权。未实现的服务检查仍明确待141。
2. SourceConsumption.consumer新增cash，作为原始银行事实的过账用途；不借用clearance核销。稳定来源是现金owner的原始CashTransaction，企业+consumer=cash+source.namespace/kind/id+coverage_key=bank_effect（版本仅读快照）唯一。现金分类/分配及它们冲销不释放此原始现金记账claim，不新增银行流。bank_effect原始claim永久保留；真正现金更正必须遵守bank-entry-extension.plan.json#/service_semantics/correction：先拒绝有效核销/分类/分配等依赖，由各owner先撤销、不级联；严格反向全部当前有效posting，保留原book/account/source，再追加完整正额replacement（两个AccountingEvent，零成本/收入），不得仅写差额。CashTransaction ID/原行/external_source_key/银行provenance不变且更正链不分支。更正复用逻辑原现金身份但另按批准请求与有效predecessor唯一消费，允许串行多次更正；不解除bank_effect普通过账去重、不把净现金差额结果当仅写差额实现。分类/关联的合法重建仍允许但零现金/AccountingEvent/LedgerPosting。不能用换版本/请求键/新分类重复确认；PH210不扩充现金更正产品或修改已有财务含义。只有cash owner的受信服务可解析/确认该来源，不从客户端classify/approved标志取得授权。此用途只新增内部去重类别，不新增账本book、成本、收入或可用工具。

PH210精确候选白名单（执行者只改前4个JSON，root单写其余）：
- docs/v1.0-development/contracts/catalog-completeness.schema.json
- docs/v1.0-development/contracts/entities.schema.json
- docs/v1.0-development/contracts/relationships.json
- docs/v1.0-development/contracts/commands.schema.json
- docs/v1.0-development/contracts/source-registry.json（root规范服务映射注解，不动态import路径）
- docs/v1.0-development/contracts/README.md
- docs/v1.0-development/fixtures/fixture.json（仅刷新实际canonical摘要，原金额/场景不改）
- docs/v1.0-development/promotion-draft/extension-freeze.json（仅受影响真实摘要/验证结果；不能抹去旧证据）
- tests/test_v1_preparation.py（保留全部旧正负例，新增精确结构/schema反例）
- docs/v1.0-development/verification.md
- project/tasks.json, project/manifest.json, docs/delivery-status.md, docs/verification.md
- project/reports/PH-210.json, project/handoffs/PH-210.md

验收：固定现有jsonschema/referencing工具环境，projectctl check；19治理；validate_contracts.py；check_contract_cases.py；check_fixture.py；manage.py test tests.test_v1_preparation（严格schema实际执行，不拿skip当通过）；BASE范围校核、独立S复核、当前全11CI和正常PR。若新SHA有其他确定依赖未在白名单，先规划补足，不能实现自扩。
新增反例至少：非空reverses_ref合法/空合法、错namespace/kind拒绝、客户端DTO塞引用拒绝；event-reversal唯一owner/方向只读投影；cash consumer合法未知consumer拒绝、稳定bank_effect排除classification/version/request_key、反例分类/核销不得被描述成原现金过账；只设计验证不冒称服务已拒绝。旧99正8负夹具及原19正37负契约案例保留；不改旧58分/业务SHA或任何客户工资/真实数据。

PH210不实施模型/服务/迁移，不部署发布，不动旧账。本规划不授权这些。具体字段schema修改及消费者名需独立复核其技术语义，无新增客户业务口径；若与既有现金更正契约冲突，应先给具体冲突，不猜改金融规则。

独立窄审：/root/ph002_candidate_review确认cashconsumer及单一冲销引用方向可行，指出并已收紧现金更正须fullreverse+fullreplacement、原bank_effect永久claim；白名单基本足够，后续Git规划最终复核另做。未运行产品。
