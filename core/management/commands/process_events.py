import json
import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from core.plugin_runtime import dispatch_pending

class Command(BaseCommand):
    help = "消费插件事务Outbox；默认单批，--watch每5秒检查"
    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--limit", type=int, default=50)
    def handle(self, *args, **options):
        while True:
            close_old_connections()
            result = dispatch_pending(options["limit"])
            if any(result.values()) or not options["watch"]: self.stdout.write(json.dumps(result, ensure_ascii=False))
            if not options["watch"] or options["once"]: break
            time.sleep(5)
