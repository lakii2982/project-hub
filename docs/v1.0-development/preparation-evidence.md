# PH135 准备验证边界与结果

起点：PH133已合入044ad549；PH134已合入e5ce5c367c194e29bb95e2c8a70acba001fa1cae。前两任务均有独立复核、精确当前8required CI和正常合入；其历史结果不代替本任务。

## 验证分层

| 层次 | 必须观察到的事实 | 当前状态 |
| --- | --- | --- |
| 规格与样本 | 严格schema及负例、附件/CSV摘要、137需求和18独立场景覆盖 | 本任务CI40623378734 schema5项及PG定向9项（含schema）无skip，99正8拒样本及3类篡改拒绝实际通过 |
| 冻结旧库 | 固定119a049归档身份，两份空库调用旧服务生成58分、审计完整、一致且不覆盖 | 本任务CI实际重建2次58分、完整性/字节一致/拒绝覆盖通过；不代表新升级 |
| SQLite兼容 | 新准备测试与原完整业务回归无意外失败；PG用例明确skip | 本次隔离完整回归通过，PG/schema明确跳过；当前CI待核对 |
| PostgreSQL资格 | 实际PG后端、两个不同backend PID、同步公司锁竞争、回滚可见性和实际唯一约束；非skip及执行标志 | 本机无PG/Docker资格。专用CI实际9定向无skip，4PG执行标志齐全，双PID锁/NOWAIT、回滚和unique通过；本地仍未验证 |
| 财务业务并发 | 核销/退款/分摊/关账跨域事务在已实现服务中的竞争、失败回滚与来源守恒 | 未实现/未验证，留正式业务任务，不能由基础设施资格替代 |
| 产品与交付 | 真实登录、四职责办理、TLS、目标Linux、DB+附件恢复、客户独立任务及签收 | 本任务未验证；原型/CI不代替客户验收 |

## 受控执行与验收

测试薄片使用既有Controller和已具备当轮资格的Kimi渠道，run_id `62f50fc1-32a9-4544-9572-4685797eabf0`；可信BASE e5ce5c3，contract digest `4abfc8de38cf8ba3a68743c8d3b3321f5cd2419fe1e00e1020ea48221226095d`。范围仅两份准备测试与独立preparation workflow。私有队列、提供方认证和原始日志不入仓；该运行在候选生成后由主控取消（原生运行562秒，取消已确认），未计为完成或PASS。主控通过GitRepo.manifest核对其仅3个允许文件后接管；发现原锁检查有线程排程假通过窗口，改为持锁期间NOWAIT明确冲突与释放后成功；回滚/唯一约束也改为不同真实连接，去掉余额字段测试写入。补强依赖缺失强制失败、3种准备输入篡改拒绝及冻结旧库CI。当前CI仍须实际验证。

完整固定验收使用既有无认证、禁网络的Verifier快照机制，不能在持有控制器/提供方/GitHub认证的宿主直接执行候选测试。定向测试不加入现有parse_acceptance白名单；由准备CI及同等隔离主控运行，保留未运行项。独立准备workflow增加检查，不替换原8required、不修改现行test workflow/门禁。

## 真实发现的后续风险

2026-10-28实际读取GitHub Dependabot：当前requirements中djangorestframework==3.16.1对应5项open告警（3高、2中）。维护方[3.18.2发行说明](https://github.com/encode/django-rest-framework/releases/tag/3.18.2)和[内容协商安全公告](https://github.com/encode/django-rest-framework/security/advisories/GHSA-33wh-fxxf-88vv)列出修复。PH137应映射P0依赖安全修复薄片，检查Python3.12/Django5.2、现有API/PAT和解析拒绝兼容，不能只换版本号或以当前业务CI全绿认定告警已消除。本准备任务不改requirements或业务口径；公开交付前必须有真实修复与当前扫描证据。

## 本次最终结果

主控使用既有Verifier，对可信BASE与全部当前差异重建无认证、禁网络快照，实际固定6项及前置migrate全部PASS。回执bd1ef002-f08e-4ad0-a316-df6245b1099b、contract digest4abfc8de...、diff digestc333cd29...保留私有runtime，未把取消的native run当作成功回执。实际60任务、19治理(2.092s)、Django检查/无迁移漂移、449测试(93.505s)及空开发库完整性{organizations:0,entries:0}通过。449项中438通过、11跳过：既有4环境限制、4PG与3schema缺可用环境；跳过不计对应领域通过。空库完整性不代表黄金旧账核对。

源码及完整metadata独立复核通过，复核者读取机器回执并核对3代码SHA。最终候选第二回执ba07ceea-9445-4efa-94c3-2d50699ee9ec固定6+migrate也实际PASS，449测试97.640s、11跳过边界同上。

当前PR75源代码提交8053697实际原8required和新增3检查均通过。新准备运行[40623378734](https://github.com/lakii2982/project-hub/actions/runs/40623378734)：schema-only5项8.356s；PG定向9项4.659s（包含5准备测试和4PG测试），均无skip；2schema+4PG执行标志在原始CI日志已核实；legacy2build/58cents/拒绝覆盖标志实际出现。PG只证明现有基础设施，未来业务UAT及生产/客户边界不变。证据/状态收口后仍须当前最终提交全部11检查及正常合入。
