# PH-134 黄金企业夹具与旧库构建器

这是冻结契约 `573ac46` 的准备资产，所有企业、人员、金额与附件均为合成数据。它不是产品种子命令或产品 UAT 通过证据；不读真实 runtime 数据库、不包含数据库副本。18 场景的业务执行、权限、原子性、浏览器与生产验证均为 `not_run`。

## 内容与读取顺序

1. `fixture.json`：固定日期、两企业、owner/finance/pm/member 四主职责、三收费模板、合成事实、91 个最终 SourceRef、18 个独立 base/命令阶段/整数分预期/来源和拒绝路径。`additional_cases` 补充分摊调差、归档迟到款、资金分配、五类 PG 竞争和恢复链；它们也未运行。
2. `command-samples.json`：99 个严格 schema 正例（包含三模板 ContractLine DTO）、8 个 schema 拒绝例。各阶段通过 sample id 关联；`dry_run=false` 仅描述未来已认证应用服务的执行意图，不授予 AI/PAT 审批、支付或锁期权限。草稿样本不是批准事实；实际执行前必须按所属域提交和独立批准。
3. `requirements-coverage.json`：137 条需求各自保留原业务规则和专项验收文字，同时保留 PH-132 对象/契约和既有 UAT 相关性。每条专项都有独立 ID；映射不证明该需求已测试。没有直接 UAT 的专项仍须单独执行。
4. `attachments/`：3 份非空纯文本演练凭据；`samples/`：CSV 正常、列重排和同来源金额冲突样本；哈希在 fixture 中。
5. `build_legacy.py` / `seed_legacy.py`：从固定产品提交 `119a04916e3e96a5e4d86516a5f13e841faf2f67` 建空 SQLite，再通过旧提交的真实 submit/review 服务形成旧账。
6. `check_fixture.py` / `verify_legacy.py`：可重跑的离线样本检查与两次旧库重建；实际结果为 `sample-verification.json`、`verification.json`。

夹具是语义化准备清单，不冒充 entities.schema.json 的正式数据库实体序列化。产品实体所需企业、状态、批准人、版本和派生金额由服务生成；这些值不能塞入客户端 DTO。命令样本只校验字段形状，不是可直接重放的运行 harness。来源解析、版本、权限、累计覆盖、同员工月、触发器、锁序和事务仍须实现任务验证。

## 分币与来源边界

每个 UAT 从独立 base 启动，不能继承其他 UAT 的变化、现金或履约。UAT-02 才包含批准合同增价；UAT-03/10 仍使用原不含税 100000 分合同，质保在该总额内。收入只有履约按批准政策确定，结算、票据、抵扣与现金均不再增加收入。三模板分别为 `fixed_milestone`、`confirmed_time`、`fixed_service_period`。

公司人工成本确认完整工资池 2400000 分，项目/非项目/未分配为 150000+150000+30000+2070000，分配不增加公司成本。101 分/60 分钟反例按 3、31、26 分钟分成 5、52、44；旧逐行原账必须保留 2+2+2+52=58，不能改成新组项目成本 57。

`net_cash_movement_cents` 是场景累计新现金，不是完整银行余额。UAT-12 期初银行 100000 单列，收款后本期现金为 50000、已声明账户余额 150000。UAT-09 连续分支现金是 31800−10000+10000−3000=28800。UAT-14 单笔已有现金 10000 争抢核销 8000，现金仍 10000，未清 2000，未分配现金 2000；核销不造银行事实。

ETC 是单独批准的剩余成本估计，不能用应收/应付余额填充；未配置是 null。预测字段是所声明场景方向的剩余金额幅度，不可当作公司带符号预测或资金余额。UAT-06 验收后尾款 20000 + 剩余承诺 60000 = 80000，付尾款后才降为 60000。

`SourceRef.version` 用于读取快照，不进入独立业务消费键。重复来源即使更换版本、请求键或渠道也不能重复确认。旧 LedgerEntry 保留 cost/cash_in/cash_out 三本原始符号；旧投影 origin=legacy、accounting_event_id=null，禁止伪造旧核算事件。

## 执行准备校核

使用现有 Python 3.12+ 与已安装 jsonschema/referencing 的环境，不安装产品依赖。以下命令在仓库根运行；`$SCHEMA_PYTHON` 指现有含 jsonschema 的解释器，`$PRODUCT_PYTHON` 指现有冻结产品依赖环境。

```sh
"$SCHEMA_PYTHON" -B docs/v1.0-development/fixtures/check_fixture.py
"$PRODUCT_PYTHON" -B docs/v1.0-development/fixtures/verify_legacy.py --repo "$PWD"
```

第一条只验证来源参照、非空附件/哈希、CSV 内容重排等价与冲突、声明算术、137 条专项存在、契约文件摘要，以及 99 个正例/8 个拒绝例。它不导入产品、不读数据库、不执行业务命令，也不把 schema default 当服务端已注入值。

第二条在系统外部临时目录建两份旧库，各实际运行冻结版 migrate、合成 seed 和 verify_integrity。比较 `business.json` 字节和旧审计链，验证已有输出目录拒绝且数据库未变；原始日志和数据库只留外部临时目录。源码归档使用 `git --no-replace-objects -c tar.umask=0002 archive`，固定归档 SHA256 为 `4ae5b5648c7716006830f6a21b73af3ac12eef4a0bd592c0d0b2dee1f30e9ef5`，不符立即停止。它不继承 PMO_DB、数据库凭据、DJANGO_SETTINGS_MODULE 或 PYTHONPATH，而是明确指定全新临时 SQLite。

单次构建也可运行：

```sh
"$PRODUCT_PYTHON" -B docs/v1.0-development/fixtures/build_legacy.py --repo "$PWD"
```

默认输出全新外部临时目录；显式 `--output-dir` 必须不存在且不在仓库内。拒绝覆盖已有目录。不要将数据库、runtime、配置、虚拟环境或日志拷入源码包。SQLite 文件含迁移执行时间，不比较两次整库文件哈希；比较逐来源 business.json 及审计链。

## 留待后续产品验收

bootstrap、条件预测初建、CSV 历史覆盖预览、内部转账原子编排和 Linux/PG 恢复有明确过程说明；冻结契约没有对应独立写命令时不发明 API。两条转账腿样本只说明字段，不能代替同事务的 InternalTransfer。UI/API/CSV/后台/附件/导出等价、角色撤销、停用、锁期、超额核销和全部服务拒绝路径均须实际执行。

旧库构建通过只证明固定旧版本可生成合成旧账，不证明新版本迁移、重复升级、切写、恢复或兼容回退。UAT-14 必须用真正 PostgreSQL 两连接与屏障；UAT-16 必须在授权 Linux/PG 目标完成数据库和非空附件联合恢复、登录及 RPO/RTO 实测。1440/390 浏览器、键盘/焦点、溢出及各状态没有在此执行。

独立审查纠正UAT14：9月关账竞争必须用9月专用来源/过账，不得绑定10月样本。主分支以屏障确保close wins并拒绝过账；独立post wins分支确认10000分成本，并要求旧watermark关账冲突、重新预检后关闭。两分支都是未来产品not_run。

UAT03的remaining_forecast_magnitude字段明确限定SET-U03本期已结算半程；S03/S04/S05为53000/21200/0。另列互斥的后半程条件范围53000，组合为106000/74200/53000，结清本期不能删掉后半程。S04显式抵扣以前，预收31800另列为可用信用，不把未清或总预测冒称银行净可用额。后续范围专项保留来源覆盖，不生成重复计划。
