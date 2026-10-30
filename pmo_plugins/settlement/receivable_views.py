"""PH-224 settlement views; replaced by the UI executor implementation."""


def _pending(name):
    from django.core.exceptions import PermissionDenied
    raise PermissionDenied('应收结算页面尚未接线：' + name)


def settlement_list(request):
    _pending('settlement_list')


def settlement_detail(request):
    _pending('settlement_detail')


def settlement_form(request):
    _pending('settlement_form')


def settlement_save(request):
    _pending('settlement_save')


def settlement_action(request):
    _pending('settlement_action')


def aging_list(request):
    _pending('aging_list')
