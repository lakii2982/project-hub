import getpass
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import Organization, Membership, Employee
from core.services import audit

class Command(BaseCommand):
    help = "交互式建立真实公司和首位负责人，不创建演练数据，不接受命令行密码"
    def add_arguments(self, parser):
        parser.add_argument("--company", required=True)
        parser.add_argument("--username", required=True)
    @transaction.atomic
    def handle(self, *args, **options):
        if Organization.objects.exists(): raise CommandError("工作区已有公司；请在系统内开通账户，避免重复初始化")
        User = get_user_model()
        password = getpass.getpass("初始密码（至少12位）：")
        if password != getpass.getpass("再次输入密码："): raise CommandError("两次密码不一致")
        user = User(username=options["username"], first_name="公司负责人")
        try: validate_password(password, user)
        except ValidationError as error: raise CommandError("；".join(error.messages))
        user.set_password(password); user.save()
        org = Organization.objects.create(name=options["company"])
        member = Membership.objects.create(organization=org, user=user, role="owner")
        Employee.objects.create(organization=org, name="公司负责人", member=member)
        audit(member, "organization.initialized", org, {"mode": "real", "currency": "CNY"})
        self.stdout.write(self.style.SUCCESS("公司工作区已建立。请登录后开通独立的项目经理/项目财务账户，并设置费率。"))
