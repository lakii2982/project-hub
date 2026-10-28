# 首个正式薄片：依赖安全修复候选

PH-137内的规划工作稿，未注册/未晋级/未实施。承接现有WP-RELEASE-PACKAGE的安全清单工作，不创造第63个产品包，也不将后续发行验收提前写成通过。

## 价值与边界

PH136已记录当前`djangorestframework==3.16.1`的5项告警，建议在长程业务开发前修复，避免在已知有缺陷的解析和认证依赖上持续构建。候选固定升级3.18.2，开工前重查官方发行/安全公告与GitHub告警；版本或兼容结论变化必须记录，不凭旧报告声称已经消除风险。

唯一依赖是PH137规划实际合入。候选新PH应为S级P0，规划合入后才ready，其他业务任务不以草稿为许可。SDK1随后依赖本任务，避免早期开发继续基于旧依赖。不得更改收入、人工成本、核销或旧账金额；不得自动合并机器人PR、降低门禁或部署。

## 拟定文件白名单

执行者：`requirements.txt`、`tests/test_drf_security_compatibility.py`。如没有固定依赖的充分支持，不为测试引入新生产依赖。

主控单写：`.github/workflows/preparation.yml`的legacy任务隔离依赖接线；`docs/v1.0-development/preparation-evidence.md`；该PH自己的report/handoff；`docs/delivery-status.md`、`docs/verification.md`、`project/tasks.json`、`project/manifest.json`。具体新PH及精确白名单须在PH137最终任务契约登记，不能实现PR自扩。

不授权API业务实现改动、绕过权限、改变令牌scope、改旧迁移、全局用户认证设置或其他workflow。若兼容测试发现必须改core/API，应先停在具体反例并用PH138独立规划，不能在本安全薄片顺手扩张范围。

## 旧构建依赖隔离

现有preparation.yml的legacy任务安装当前requirements，只因当前仍与freeze119a049一致才正确。升级后必须从固定`119a04916e3e96a5e4d86516a5f13e841faf2f67`提取其requirements，在新的隔离venv安装，再执行既有旧构建器两次。产品/PG/schema测试使用新依赖；旧构建只用冻结依赖且没有公共服务/真实数据，不与新产品venv混用。既定源码archive校验、58分、业务SHA、拒绝覆盖/DB不变断言均保留；不能通过更新旧预期适应新行为。

## 验证与实际交付

1. 用Python3.12/Django5.2实际安装固定版本并记录运行版本；依赖完整性检查与全量现有测试。
2. 现有PAT身份、scope/项目/期限、停用/撤销和字段裁剪拒绝仍有效；匿名/跨企业不得因渲染器或异常处理读到受保护数据。
3. JSON解析拒绝攻击者指定codec；Accept异常/深参数输入有有界确定性测试，不用易抖动的单次秒表断言冒充DoS消除证明。请求体限制覆盖实际启用parser及项目配置，不能仅测试上游工具函数。
4. 对实际启用renderer/token路径做兼容测试。未启用AdminRenderer或DRF TokenProxy要如实标不适用；不能把未使用路径写成生产利用复现。已固定上游版本仍需记录供应链修复依据。
5. 新依赖下SQLite、真实PG全量与准备schema/fixture验证；冻结旧依赖下两次legacy重建与旧58分/SHA一致。MCP/PAT相关旧检查保持全部通过。
6. 独立S复核最终差异、现有8required+3准备CI的当前head全部通过，所有讨论解决后正常PR合入。无版本发布、目标环境部署或客户验收声明。

DoD：固定依赖与具体兼容/拒绝测试、旧构建隔离、实际版本及命令证据、独立复核、当前CI/merge、报告/交接齐全。仅requirements换数字或只看到旧CI绿不算完成。官方参考：[3.18.2发行说明](https://github.com/encode/django-rest-framework/releases/tag/3.18.2)、[安全公告](https://github.com/encode/django-rest-framework/security/advisories/GHSA-33wh-fxxf-88vv)；当前实施时仍需重新核验。
