import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.models import Membership
from core.reminder_services import scan_reminders


class Command(BaseCommand):
    help = "按当前成员权限输出只读到期提醒；不写收件箱、不发送通知"

    def add_arguments(self, parser):
        parser.add_argument("--member", type=int, required=True, help="当前成员 ID")
        parser.add_argument("--as-of", dest="as_of")
        parser.add_argument("--period")
        parser.add_argument("--sync", action="store_true",
                            help="物化当前成员的内部通知快照；默认保持只读")

    def handle(self, *args, **options):
        try:
            member = Membership.objects.get(pk=options['member'])
            if options['sync']:
                from core.notification_services import sync_notifications
                result = sync_notifications(
                    member, as_of=options['as_of'], period=options['period'])
            else:
                result = scan_reminders(
                    member, as_of=options['as_of'], period=options['period'])
        except Membership.DoesNotExist:
            raise CommandError("成员不存在")
        except (PermissionDenied, ValidationError) as error:
            raise CommandError('；'.join(getattr(error, 'messages', [str(error)])))
        self.stdout.write(json.dumps(result, ensure_ascii=False))
