# ADR 0002 · 最小领域插件元数据与注册快照

状态：PH-137已冻结格式，PH-001实现候选；当前PR通过且正常合入后成为代码基线。SDK-2的入口门控、路由接线与SDK-3的迁移扫描尚待独立薄片，不能从本ADR推断已经完成。

保留Django单体、现有Plugin Registry与三个叶插件。新业务App声明领域元数据，支持后续合同、结算、成本、现金等模块拥有自己的事实；本片不迁移旧表、不新增模型或运行服务，不建设动态插件商店或热装框架。

## 唯一身份与字段来源

旧Plugin.manifest仍恰好八字段，版本、内核兼容范围、插件依赖和default_enabled只在此维护。独立domain_metadata仍恰好六字段，定义以已冻结的SDK1 JSON schema为准：schema_version、plugin_id、django_app、contracts、routes、migration_dependencies。产品不增加jsonschema运行依赖。plugin_id引用manifest.id，namespace直接派生；App label独立且唯一，表前缀由label派生。

命令、查询、事件ID引用已有canonical catalog，不复制DTO或金额字段。routes仅声明静态相对路径与访问意图，不执行handler或提供审批/支付/锁期工具。迁移依赖是声明，不是数据库迁移扫描通过。

## 实现接口与受控上下文

validate_domain_metadata(metadata, manifest, catalog, installed_apps)完成严格结构及语义校验，返回深拷贝。缺少catalog或部署上下文明确拒绝，不降级为仅检查键集合。catalog使用canonical contract_catalog列表或包裹对象；installed_apps来自发行/部署注册信息，接受AppConfig列表或label索引。索引值可以是module字符串，或严格的module/plugin_id对象。后者用于App label不同于插件ID的合法情况，不增加domain_metadata字段，也不形成另一份业务事实。

业务migration label先解析成受控插件身份，再核对manifest.requires；共享App也必须真实在安装上下文中。build_domain_registry(installed_descriptors)仅在原安装索引已有label且模块一致时补齐该身份，不能把metadata声明当作安装证据。两个接口不从元数据字符串导入模块，不联网、不写数据库、审计、路由或事件。

descriptor包含plugin_id、metadata、manifest、catalog、installed_apps。构建先复核全部候选及冲突，再发布不可变快照。namespace、App label或App module重复均拒绝冲突双方，即使一方另有格式错误；路径按派生挂载前缀及path检查，不能靠改路由name/access绕过。坏依赖、版本不兼容或环影响整条依赖链，无关项保留。版本与manifest图规则复用既有Registry.validate/validate_graph，不另维护第二套版本规则。

快照domains按插件ID包含plugin_id、label、metadata、manifest；字典递归只读、列表转tuple，不保留活Plugin实例。errors只含合法插件ID或固定占位键及固定错误类别，排序去重；后续修改输入或普通属性赋值不能改变快照。此不可变性用于避免意外修改，不把同进程受信Python代码宣称为安全沙箱。

## 示例与兼容边界

example_domain是随发行受信的独立App，默认停用，零模型、零迁移。测试可用override_settings装载真实AppConfig，生产settings与URL不接线。旧八字段manifest、旧URL、旧表/主键与三个叶插件源码不修改。

升级沿用旧is_enabled规则：已有PluginState且版本不匹配时不可用；无状态行仍取default_enabled；查询不自动创建状态或确认升级。示例版本升级不改变metadata.schema_version，不重算历史账。

## 验证与后续

本片验收包含schema结构与Python拒绝用例、真实AppConfig、依赖图/冲突双方/不可变/不导入不联网不写库、版本有无状态分支、独立示例及旧叶插件回归。实际数量和命令以PH-001报告与当次CI为准，不能把格式设计当作运行门控或客户验收。

SDK-1完成后才进入PH-002统一入口门控，再进入PH-140迁移与重放资格。新增业务仍需各自的权限、期间、来源、幂等、公司锁和账本不变式；SDK元数据不能代替业务服务校验。任何字段格式破坏变化另开版本及显式适配，任何范围增加先独立规划。
