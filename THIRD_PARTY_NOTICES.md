# 第三方组件与来源

本项目自身以Apache-2.0发布。下列组件通过依赖安装使用；各组件许可和版权属于原作者，不被本项目许可覆盖。安装包/容器应保留上游发行版内的LICENSE、NOTICE和dist-info许可文件。

| 组件 | 来源 | 许可概况 |
| --- | --- | --- |
| Django | https://github.com/django/django | BSD-3-Clause |
| Django REST Framework | https://github.com/encode/django-rest-framework | BSD-3-Clause |
| django-axes | https://github.com/jazzband/django-axes | MIT |
| WhiteNoise | https://github.com/evansd/whitenoise | MIT |
| Gunicorn | https://github.com/benoitc/gunicorn | MIT |
| psycopg | https://github.com/psycopg/psycopg | LGPL-3.0，二进制分发的额外原生依赖以其许可证为准 |
| pluggy | https://github.com/pytest-dev/pluggy | MIT |
| Python MCP SDK | https://github.com/modelcontextprotocol/python-sdk | MIT |
| httpx | https://github.com/encode/httpx | BSD-3-Clause |
| pydantic | https://github.com/pydantic/pydantic | MIT |
| PostgreSQL / Caddy | https://www.postgresql.org/ / https://github.com/caddyserver/caddy | PostgreSQL License / Apache-2.0，Compose镜像独立分发 |

准确版本与全部传递依赖分别由requirements.txt和integrations/mcp/requirements.txt锁定。CI保留pip inspect的实际安装依赖记录；本表不是完整二进制SBOM或所有传递许可的替代。正式二进制或镜像发布前核对对应发行物的许可清单与依赖安全结果。

静态资源由Django collectstatic产生时保留其第三方许可文件。仓库自有UI使用系统字体，没有分发商用字体包。开源产品的行为参考与采用其业务源码是不同事项，研究记录见docs/open-source-review.md。
