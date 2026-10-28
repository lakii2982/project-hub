# SDK-1字段与兼容契约候选

PH-137工作稿，BASE为`f1115cfd043a9f3a9a7ffc05d6a88d772e324920`。尚未独立审阅、规划PR或合入；不解除PH-001的proposed/blocker，不作为实现许可。这里只冻结最小必要元数据，运行门控、路由接线和迁移扫描分别交SDK-2/3。

## 元数据唯一来源

现有`Plugin.manifest`继续恰好8字段，版本、内核兼容范围、依赖版本和公司默认启用值只维护在那里。三个叶插件不新增字段、不改变加载方式。业务App另导出`domain_metadata`，对应本目录`sdk1-metadata.schema.json`；不得把两份字典合并后交旧Registry，也不得对旧manifest放宽unknown字段检查。

| 元数据字段 | 类型/约束 | 唯一来源与含义 |
| --- | --- | --- |
| schema_version | 严格字符串`1` | 本描述格式版本；不是产品或插件版本，不接受整数/bool |
| plugin_id | 与旧manifest.id完全相同 | 引用现有插件身份；命名空间直接由此派生，不再维护第二个namespace字段 |
| django_app.module | 固定`pmo_plugins.`下Python模块路径 | 受信随发行安装的App；不得来自URL、用户上传或请求参数 |
| django_app.label | 合法Django label，全注册表唯一 | 表前缀派生为`label_`，不再另维护data_namespace/table_prefix |
| contracts.commands/queries/events | 三个必有数组，组内唯一、最多128项 | 只引用既定目录中的版本化ID；请求、返回、事件字段仍只取PH132 schema，不复制DTO |
| routes | 最多64条`name/path/access` | name域内唯一；同时按派生挂载前缀＋path检查唯一，同域同path即使name/access不同也拒绝；schema不能表达按属性唯一，此项由Python注册语义校验保证。path是静态相对段，可为空；本薄片不支持converter、正则、重定向或可执行handler字符串。access只作声明，不能声称已强制执行 |
| migration_dependencies | 最多64个唯一App label | 必须是实际已声明依赖的业务App或受控共享App；不能自依赖；只是声明，不冒充迁移扫描已经通过 |

所有对象拒绝未知字段；所有必填值缺失拒绝；禁止自动填默认后掩盖缺失。schema不请求网络。结构schema之外还须做注册语义校验：plugin_id一致，module与真实AppConfig一致，重复App/namespace/route拒绝，contract ID在正确类别目录存在，旧manifest版本/依赖兼容校验继续有效。依赖图先全量检查再发布注册快照，失败不留半注册；错误只包含插件及错误类别，不包含环境变量或凭据。

命名空间的有效值直接取plugin_id；URL挂载前缀候选`/modules/<plugin_id>/`由SDK-2统一派生。SDK-1不在pmo/urls.py接线。已有URL、表名、主键、账本和8字段manifest完全保留。需要动态资源路径的业务路由另由SDK-2契约显式冻结，不能执行者临场放宽静态path格式。

## 最小实现面与兼容

PH-001候选执行文件限定`core/plugin_sdk/__init__.py`、`metadata.py`、`registry.py`、独立示例App的`__init__.py/apps.py/plugin.py/domain_metadata.py`及`tests/test_plugin_sdk.py`。依赖使用已固定Python/packaging/Django，产品运行时不新增jsonschema、构建链或第三方插件安装器。JSON schema用于设计验证；实现中的严格校验必须与样例/反例一致，不能只检查字典键集合。

`validate_domain_metadata(metadata, manifest, catalog, installed_apps)`返回经校验的描述副本，不导入请求指定的模块、不执行业务命令、不写库；`build_domain_registry(installed_descriptors)`返回不可变注册快照和按插件分组的错误。依赖断裂插件及其依赖方不可用，无关叶插件不受影响；同一namespace/app/route冲突的双方均拒绝，禁止按加载顺序悄悄覆盖。实现细节由执行者选择，接口语义由此固定。

示例是受信独立Django App，默认停用，不增加业务表/迁移/真实财务能力；测试通过独立AppConfig和受控override_settings装载，生产settings不接线。提供示例单独版本升级测试，断言另一示例及三个现有叶插件源码哈希未变，旧manifest/12项插件回归通过。只改示例版本不改变metadata.schema_version；仅当已有PluginState且其version与新manifest.version不同时，旧规则才判停用并要求owner重新启用；无PluginState行仍取default_enabled，不把版本变化改成无条件停用。SDK不能自动创建状态或确认升级。

描述格式1内新增必填/状态含义/未知值容忍改变均视为破坏变更，另开格式2及显式适配。命令/查询/事件仍遵守原版本契约；metadata不定义收入/费用等业务状态，也不让capabilities变成未经实现的权限引擎。未来改变表所有权只追加迁移并经SDK-3/领域薄片；不在本任务搬旧表。

## 固定反例与完成定义

| 输入/动作 | 期望 |
| --- | --- |
| 给旧manifest加domain_metadata/缺少既有字段 | 旧Registry继续拒绝 |
| schema_version传1/true；新增未知字段；缺queries；重复contract | 元数据拒绝，0数据库/事件/路由变化 |
| 两插件同App label/namespace/路由全名；同域不同name但挂载后path相同 | 冲突项均不可注册，无关项保留，重复构建结果一致 |
| 指向不存在或错误类别的contract ID、HTTP ref、跨目录路径 | 拒绝，0网络请求，不建立伪schema副本 |
| 未安装依赖、不兼容内核/插件版本、循环或自依赖 | 拒绝受影响链，三个旧叶插件仍遵守原兼容规则 |
| 校验后调用者篡改原dict/list | 已构建注册快照不变 |
| 只升级独立示例App | 另一插件源码不变，旧URL/账本/费率/历史触发器不变 |

未来PH-001至少实际运行现有4项固定验收；补`verify_integrity --json`、专项插件反例、示例升级源码哈希比较、schema与Python实现一致用例。此计划涉及验收/白名单调整，须先落入PH137 tasks/spec规划再实现。独立S复核最终候选并通过当前全部CI、正常PR合入、报告/交接后才done；schema设计验证不能代替这些产品测试。

候选依赖顺序：PH137规划合入 → P0依赖安全薄片通过 → PH001 SDK-1 → PH002统一门控/静态路由 → 新SDK-3迁移与重放薄片 → WP-CORE。WP-SDK应覆盖三张PH，不能仅PH001完成就算整个SDK完成。SDK-4旧delivery/time抽离由对应领域薄片承接，保留表与适配边界。具体新PH编号及状态待PH137最终登记；本稿不派遣。
