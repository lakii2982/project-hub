from django.apps import AppConfig


class ExampleDomainConfig(AppConfig):
    # 受信随发行安装的示例App；label 与 domain_metadata.django_app.label 一致。
    name = "pmo_plugins.example_domain"
    label = "example_domain"
    verbose_name = "示例领域插件"
